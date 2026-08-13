"""
ShopStream Supplier Backend Service
Event-driven inventory coordination system
"""

import os
import json
import uuid
from datetime import datetime, timezone
from typing import List, Optional
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field
from azure.storage.queue import QueueClient, TextBase64EncodePolicy
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure structured logging
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="ISO"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        structlog.processors.JSONRenderer()
    ],
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger()

# Azure Storage Queue configuration
AZURE_STORAGE_CONNECTION_STRING = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
QUEUE_NAME = "inventory-events"
STOCK_THRESHOLD = int(os.getenv("STOCK_THRESHOLD", "10"))

# Global queue client
queue_client = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager"""
    global queue_client
    
    # Startup
    if AZURE_STORAGE_CONNECTION_STRING:
        try:
            queue_client = QueueClient.from_connection_string(
                AZURE_STORAGE_CONNECTION_STRING,
                QUEUE_NAME,
                # Azure Functions' queue trigger expects base64-encoded messages;
                # without this policy the host cannot decode them and every event
                # dead-letters to the poison queue without the function ever running.
                message_encode_policy=TextBase64EncodePolicy(),
            )
            # Create queue if it doesn't exist
            queue_client.create_queue()
            logger.info("Azure Storage Queue initialized", queue_name=QUEUE_NAME)
        except Exception as e:
            logger.error("Failed to initialize Azure Storage Queue", error=str(e))
    else:
        logger.warning("Azure Storage connection string not configured")
    
    yield
    
    # Shutdown
    logger.info("ShopStream Supplier Backend shutting down")


# Pydantic models
class Product(BaseModel):
    id: str = Field(..., description="Product unique identifier")
    name: str = Field(..., description="Product name")
    stock_quantity: int = Field(..., ge=0, description="Current stock quantity")
    price: float = Field(..., gt=0, description="Product price")
    supplier_id: str = Field(..., description="Supplier identifier")


class ProductUpdate(BaseModel):
    stock_quantity: int = Field(..., ge=0, description="New stock quantity")


class InventoryEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    correlation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_type: str = Field(default="stock_below_threshold")
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    product_id: str
    product_name: str
    current_stock: int
    threshold: int
    supplier_id: str
    suggested_order_quantity: int


# Initialize FastAPI app
app = FastAPI(
    title="ShopStream Supplier Backend",
    description="Event-driven inventory coordination system",
    version="1.0.0",
    lifespan=lifespan
)

# In-memory product store (for demo purposes)
products_db = {
    "prod-001": Product(
        id="prod-001",
        name="Wireless Headphones",
        stock_quantity=5,
        price=99.99,
        supplier_id="supp-001"
    ),
    "prod-002": Product(
        id="prod-002",
        name="Bluetooth Speaker",
        stock_quantity=15,
        price=49.99,
        supplier_id="supp-002"
    ),
    "prod-003": Product(
        id="prod-003",
        name="USB-C Cable",
        stock_quantity=3,
        price=12.99,
        supplier_id="supp-001"
    ),
}


async def emit_inventory_event(product: Product, correlation_id: str = None) -> str:
    """Emit inventory event to Azure Storage Queue"""
    if not queue_client:
        logger.warning("Queue client not initialized - event not sent")
        return None
    
    if correlation_id is None:
        correlation_id = str(uuid.uuid4())
    
    # Calculate suggested order quantity (2x threshold for safety stock)
    suggested_quantity = max(STOCK_THRESHOLD * 2 - product.stock_quantity, STOCK_THRESHOLD)
    
    event = InventoryEvent(
        correlation_id=correlation_id,
        product_id=product.id,
        product_name=product.name,
        current_stock=product.stock_quantity,
        threshold=STOCK_THRESHOLD,
        supplier_id=product.supplier_id,
        suggested_order_quantity=suggested_quantity
    )
    
    try:
        # Send message to queue
        message_content = event.model_dump_json()
        queue_client.send_message(message_content)
        
        logger.info(
            "Inventory event emitted",
            event_id=event.event_id,
            correlation_id=correlation_id,
            product_id=product.id,
            current_stock=product.stock_quantity,
            threshold=STOCK_THRESHOLD,
            queue_name=QUEUE_NAME
        )
        
        return event.event_id
        
    except Exception as e:
        logger.error(
            "Failed to emit inventory event",
            correlation_id=correlation_id,
            product_id=product.id,
            error=str(e)
        )
        raise HTTPException(status_code=500, detail="Failed to emit inventory event")


@app.get("/")
async def root():
    """Health check endpoint"""
    return {
        "service": "ShopStream Supplier Backend",
        "status": "running",
        "queue_configured": queue_client is not None,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


@app.get("/products", response_model=List[Product])
async def get_products():
    """Get all products"""
    logger.info("Fetching all products", count=len(products_db))
    return list(products_db.values())


@app.get("/products/{product_id}", response_model=Product)
async def get_product(product_id: str):
    """Get product by ID"""
    if product_id not in products_db:
        logger.warning("Product not found", product_id=product_id)
        raise HTTPException(status_code=404, detail="Product not found")
    
    product = products_db[product_id]
    logger.info("Product retrieved", product_id=product_id, stock=product.stock_quantity)
    return product


@app.put("/products/{product_id}/stock", response_model=Product)
async def update_product_stock(
    product_id: str, 
    update: ProductUpdate, 
    background_tasks: BackgroundTasks
):
    """Update product stock and emit event if below threshold"""
    if product_id not in products_db:
        logger.warning("Product not found for stock update", product_id=product_id)
        raise HTTPException(status_code=404, detail="Product not found")
    
    correlation_id = str(uuid.uuid4())
    product = products_db[product_id]
    old_stock = product.stock_quantity
    
    # Update stock
    product.stock_quantity = update.stock_quantity
    
    logger.info(
        "Product stock updated",
        product_id=product_id,
        old_stock=old_stock,
        new_stock=update.stock_quantity,
        correlation_id=correlation_id
    )
    
    # Check if stock is below threshold and emit event
    if update.stock_quantity < STOCK_THRESHOLD:
        logger.warning(
            "Stock below threshold - emitting event",
            product_id=product_id,
            stock=update.stock_quantity,
            threshold=STOCK_THRESHOLD,
            correlation_id=correlation_id
        )
        
        background_tasks.add_task(emit_inventory_event, product, correlation_id)
    
    return product


@app.post("/products/{product_id}/simulate-sale")
async def simulate_sale(product_id: str, quantity: int = 1, background_tasks: BackgroundTasks = None):
    """Simulate a sale to demonstrate the system"""
    if product_id not in products_db:
        raise HTTPException(status_code=404, detail="Product not found")
    
    product = products_db[product_id]
    if product.stock_quantity < quantity:
        raise HTTPException(status_code=400, detail="Insufficient stock")
    
    correlation_id = str(uuid.uuid4())
    old_stock = product.stock_quantity
    product.stock_quantity -= quantity
    
    logger.info(
        "Sale simulated",
        product_id=product_id,
        quantity_sold=quantity,
        old_stock=old_stock,
        new_stock=product.stock_quantity,
        correlation_id=correlation_id
    )
    
    # Check if stock is below threshold
    if product.stock_quantity < STOCK_THRESHOLD:
        logger.warning(
            "Stock below threshold after sale - emitting event",
            product_id=product_id,
            stock=product.stock_quantity,
            threshold=STOCK_THRESHOLD,
            correlation_id=correlation_id
        )
        
        if background_tasks:
            background_tasks.add_task(emit_inventory_event, product, correlation_id)
        else:
            await emit_inventory_event(product, correlation_id)
    
    return {
        "message": "Sale completed",
        "product_id": product_id,
        "quantity_sold": quantity,
        "remaining_stock": product.stock_quantity,
        "below_threshold": product.stock_quantity < STOCK_THRESHOLD,
        "correlation_id": correlation_id
    }


@app.get("/queue/status")
async def get_queue_status():
    """Get queue status and message count"""
    if not queue_client:
        return {"error": "Queue client not initialized"}
    
    try:
        properties = queue_client.get_queue_properties()
        return {
            "queue_name": QUEUE_NAME,
            "approximate_message_count": properties.approximate_message_count
        }
    except Exception as e:
        logger.error("Failed to get queue status", error=str(e))
        return {"error": str(e)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
