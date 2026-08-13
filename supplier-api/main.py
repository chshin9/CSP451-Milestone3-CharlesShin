"""
Supplier API Microservice
Simulates supplier order processing for ShopStream
"""

import os
import uuid
from datetime import datetime, timezone
from typing import Dict, Any
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel, Field
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

import logging as _stdlib_logging
import sys as _stdlib_sys

_stdlib_logging.basicConfig(
    format="%(message)s",
    stream=_stdlib_sys.stdout,
    level=_stdlib_logging.INFO,
    force=True,
)

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

# Configuration
SUPPLIER_ID = os.getenv("SUPPLIER_ID", "ACME-SUPPLIER-001")
PROCESSING_TIME_SECONDS = int(os.getenv("PROCESSING_TIME_SECONDS", "2"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager"""
    # Startup
    logger.info("Supplier API starting up", supplier_id=SUPPLIER_ID)
    yield
    # Shutdown
    logger.info("Supplier API shutting down", supplier_id=SUPPLIER_ID)


# Pydantic models
class OrderRequest(BaseModel):
    product_id: str = Field(..., description="Product identifier")
    product_name: str = Field(..., description="Product name")
    quantity: int = Field(..., gt=0, description="Quantity to order")
    supplier_id: str = Field(..., description="Supplier identifier")
    priority: str = Field(default="normal", description="Order priority")
    correlation_id: str = Field(None, description="Correlation ID for tracing")


class OrderResponse(BaseModel):
    order_id: str = Field(..., description="Generated order ID")
    status: str = Field(..., description="Order status")
    estimated_delivery_days: int = Field(..., description="Estimated delivery time")
    total_cost: float = Field(..., description="Total order cost")
    confirmation_number: str = Field(..., description="Supplier confirmation number")
    correlation_id: str = Field(..., description="Correlation ID for tracing")
    processed_at: str = Field(..., description="Processing timestamp")
    supplier_id: str = Field(..., description="Supplier identifier")


class SupplierInfo(BaseModel):
    supplier_id: str
    name: str
    location: str
    contact_email: str
    processing_capacity: str
    specialties: list[str]


# Initialize FastAPI app
app = FastAPI(
    title="Supplier API",
    description="Simulated supplier order processing service",
    version="1.0.0",
    lifespan=lifespan
)

# Simulated supplier catalog with pricing
supplier_catalog = {
    "prod-001": {"name": "Wireless Headphones", "unit_cost": 45.00, "delivery_days": 3},
    "prod-002": {"name": "Bluetooth Speaker", "unit_cost": 25.00, "delivery_days": 2},
    "prod-003": {"name": "USB-C Cable", "unit_cost": 5.00, "delivery_days": 1},
    "default": {"name": "Generic Product", "unit_cost": 10.00, "delivery_days": 5},
}

# Order processing history (in-memory for demo)
order_history = {}


@app.get("/")
async def root():
    """Health check and service info"""
    return {
        "service": "Supplier API",
        "supplier_id": SUPPLIER_ID,
        "status": "operational",
        "capabilities": ["order_processing", "inventory_check", "delivery_estimation"],
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


@app.get("/info", response_model=SupplierInfo)
async def get_supplier_info():
    """Get supplier information"""
    return SupplierInfo(
        supplier_id=SUPPLIER_ID,
        name="ACME Electronics Supplier",
        location="Toronto, ON, Canada",
        contact_email="orders@acme-electronics.com",
        processing_capacity="1000 orders/day",
        specialties=["Electronics", "Audio Equipment", "Cables & Accessories"]
    )


@app.post("/order", response_model=OrderResponse)
async def process_order(
    order: OrderRequest,
    x_correlation_id: str = Header(None, alias="X-Correlation-ID")
):
    """Process supplier order request"""
    
    # Use correlation ID from header or order payload
    correlation_id = x_correlation_id or order.correlation_id or str(uuid.uuid4())
    
    # Generate order ID
    order_id = f"ORD-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{str(uuid.uuid4())[:8].upper()}"
    
    logger.info(
        "Order received",
        order_id=order_id,
        correlation_id=correlation_id,
        product_id=order.product_id,
        quantity=order.quantity,
        supplier_id=order.supplier_id,
        priority=order.priority
    )
    
    # Validate supplier ID
    if order.supplier_id not in [SUPPLIER_ID, "supp-001", "supp-002"]:
        logger.warning(
            "Invalid supplier ID",
            correlation_id=correlation_id,
            requested_supplier=order.supplier_id,
            valid_supplier=SUPPLIER_ID
        )
        raise HTTPException(
            status_code=400,
            detail=f"Invalid supplier ID. This supplier handles: {SUPPLIER_ID}"
        )
    
    # Get product info from catalog
    product_info = supplier_catalog.get(order.product_id, supplier_catalog["default"])
    
    # Calculate costs and delivery
    unit_cost = product_info["unit_cost"]
    total_cost = unit_cost * order.quantity
    delivery_days = product_info["delivery_days"]
    
    # Apply priority adjustments
    if order.priority == "urgent":
        delivery_days = max(1, delivery_days - 1)
        total_cost *= 1.2  # 20% rush fee
    elif order.priority == "low":
        delivery_days += 2
        total_cost *= 0.95  # 5% discount for flexible delivery
    
    # Generate confirmation
    confirmation_number = f"CONF-{str(uuid.uuid4())[:12].upper()}"
    
    # Create response
    response = OrderResponse(
        order_id=order_id,
        status="confirmed",
        estimated_delivery_days=delivery_days,
        total_cost=round(total_cost, 2),
        confirmation_number=confirmation_number,
        correlation_id=correlation_id,
        processed_at=datetime.now(timezone.utc).isoformat(),
        supplier_id=SUPPLIER_ID
    )
    
    # Store order in history
    order_history[order_id] = {
        "request": order.model_dump(),
        "response": response.model_dump(),
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    
    logger.info(
        "Order processed successfully",
        order_id=order_id,
        correlation_id=correlation_id,
        status=response.status,
        total_cost=response.total_cost,
        delivery_days=response.estimated_delivery_days,
        confirmation_number=confirmation_number
    )
    
    return response


@app.get("/orders/{order_id}")
async def get_order_status(order_id: str):
    """Get order status by ID"""
    if order_id not in order_history:
        logger.warning("Order not found", order_id=order_id)
        raise HTTPException(status_code=404, detail="Order not found")
    
    order_data = order_history[order_id]
    correlation_id = order_data["response"]["correlation_id"]
    
    logger.info("Order status retrieved", order_id=order_id, correlation_id=correlation_id)
    
    return order_data


@app.get("/orders")
async def get_recent_orders(limit: int = 10):
    """Get recent orders"""
    recent_orders = list(order_history.values())[-limit:]
    
    logger.info("Recent orders retrieved", count=len(recent_orders))
    
    return {
        "orders": recent_orders,
        "total_count": len(order_history),
        "showing": len(recent_orders)
    }


@app.get("/catalog")
async def get_catalog():
    """Get supplier catalog with pricing"""
    logger.info("Catalog requested")
    
    return {
        "supplier_id": SUPPLIER_ID,
        "catalog": supplier_catalog,
        "currency": "CAD",
        "last_updated": datetime.now(timezone.utc).isoformat()
    }


@app.post("/webhook/test")
async def test_webhook(payload: Dict[Any, Any]):
    """Test webhook endpoint for Azure Function testing"""
    correlation_id = payload.get("correlation_id", str(uuid.uuid4()))
    
    logger.info(
        "Test webhook received",
        correlation_id=correlation_id,
        payload_keys=list(payload.keys())
    )
    
    return {
        "status": "received",
        "correlation_id": correlation_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "supplier_id": SUPPLIER_ID
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
