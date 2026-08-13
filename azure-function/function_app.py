"""
Azure Function - Inventory Event Processor
Processes inventory events from Azure Storage Queue and calls Supplier API
"""

import os
import json
import logging
import requests
from typing import Dict, Any
from datetime import datetime, timezone

import azure.functions as func
import structlog
from pydantic import BaseModel, ValidationError

import logging as _stdlib_logging
import sys as _stdlib_sys

_stdlib_logging.basicConfig(
    format="%(message)s",
    stream=_stdlib_sys.stdout,
    level=_stdlib_logging.INFO,
    force=True,
)

# Configure structured logging for Azure Functions
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

# Configuration from environment variables
SUPPLIER_API_URL = os.getenv("SUPPLIER_API_URL", "http://localhost:8001")
RETRY_ATTEMPTS = int(os.getenv("RETRY_ATTEMPTS", "3"))
TIMEOUT_SECONDS = int(os.getenv("TIMEOUT_SECONDS", "30"))

# Create Function App
app = func.FunctionApp()


class InventoryEvent(BaseModel):
    """Inventory event model matching backend schema"""
    event_id: str
    correlation_id: str
    event_type: str
    timestamp: str
    product_id: str
    product_name: str
    current_stock: int
    threshold: int
    supplier_id: str
    suggested_order_quantity: int


class SupplierOrderRequest(BaseModel):
    """Supplier order request model"""
    product_id: str
    product_name: str
    quantity: int
    supplier_id: str
    priority: str = "normal"
    correlation_id: str


def call_supplier_api(order_request: SupplierOrderRequest, correlation_id: str) -> Dict[str, Any]:
    """Call the Supplier API with retry logic"""
    
    url = f"{SUPPLIER_API_URL}/order"
    headers = {
        "Content-Type": "application/json",
        "X-Correlation-ID": correlation_id
    }
    
    payload = order_request.model_dump()
    
    logger.info(
        "Calling Supplier API",
        url=url,
        correlation_id=correlation_id,
        product_id=order_request.product_id,
        quantity=order_request.quantity
    )
    
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            response = requests.post(
                url,
                json=payload,
                headers=headers,
                timeout=TIMEOUT_SECONDS
            )
            
            if response.status_code == 200:
                response_data = response.json()
                logger.info(
                    "Supplier API call successful",
                    correlation_id=correlation_id,
                    attempt=attempt,
                    order_id=response_data.get("order_id"),
                    status=response_data.get("status"),
                    total_cost=response_data.get("total_cost")
                )
                return response_data
            else:
                logger.warning(
                    "Supplier API call failed",
                    correlation_id=correlation_id,
                    attempt=attempt,
                    status_code=response.status_code,
                    response_text=response.text
                )
                
        except requests.exceptions.RequestException as e:
            logger.error(
                "Supplier API call exception",
                correlation_id=correlation_id,
                attempt=attempt,
                error=str(e),
                error_type=type(e).__name__
            )
            
        # Wait before retry (except on last attempt)
        if attempt < RETRY_ATTEMPTS:
            import time
            time.sleep(2 ** attempt)  # Exponential backoff
    
    # All attempts failed
    logger.error(
        "All Supplier API call attempts failed",
        correlation_id=correlation_id,
        total_attempts=RETRY_ATTEMPTS
    )
    raise Exception(f"Failed to call Supplier API after {RETRY_ATTEMPTS} attempts")


@app.queue_trigger(
    arg_name="msg", 
    queue_name="inventory-events",
    connection="AzureWebJobsStorage"
)
def inventory_event_processor(msg: func.QueueMessage) -> None:
    """
    Azure Function triggered by inventory events in Storage Queue
    Processes the event and calls Supplier API to place orders
    """
    
    try:
        # Parse the message
        message_body = msg.get_body().decode('utf-8')
        logger.info("Inventory event received", message_body=message_body)
        
        # Parse JSON message
        try:
            event_data = json.loads(message_body)
        except json.JSONDecodeError as e:
            logger.error("Failed to parse message JSON", error=str(e), message_body=message_body)
            raise
        
        # Validate event structure
        try:
            inventory_event = InventoryEvent(**event_data)
        except ValidationError as e:
            logger.error("Invalid inventory event format", error=str(e), event_data=event_data)
            raise
        
        correlation_id = inventory_event.correlation_id
        
        logger.info(
            "Processing inventory event",
            event_id=inventory_event.event_id,
            correlation_id=correlation_id,
            product_id=inventory_event.product_id,
            current_stock=inventory_event.current_stock,
            threshold=inventory_event.threshold,
            suggested_quantity=inventory_event.suggested_order_quantity
        )
        
        # Create supplier order request
        supplier_order = SupplierOrderRequest(
            product_id=inventory_event.product_id,
            product_name=inventory_event.product_name,
            quantity=inventory_event.suggested_order_quantity,
            supplier_id=inventory_event.supplier_id,
            priority="normal",  # Could be "urgent" if stock is critically low
            correlation_id=correlation_id
        )
        
        # Determine priority based on stock level
        if inventory_event.current_stock <= inventory_event.threshold // 2:
            supplier_order.priority = "urgent"
            logger.warning(
                "Critical stock level - setting urgent priority",
                correlation_id=correlation_id,
                current_stock=inventory_event.current_stock,
                threshold=inventory_event.threshold
            )
        
        # Call Supplier API
        try:
            supplier_response = call_supplier_api(supplier_order, correlation_id)
            
            logger.info(
                "Inventory event processed successfully",
                correlation_id=correlation_id,
                event_id=inventory_event.event_id,
                order_id=supplier_response.get("order_id"),
                order_status=supplier_response.get("status"),
                estimated_delivery=supplier_response.get("estimated_delivery_days"),
                total_cost=supplier_response.get("total_cost")
            )
            
        except Exception as e:
            logger.error(
                "Failed to process inventory event",
                correlation_id=correlation_id,
                event_id=inventory_event.event_id,
                error=str(e),
                error_type=type(e).__name__
            )
            # Re-raise to trigger function failure and potential retry
            raise
        
    except Exception as e:
        # Log the error with context
        correlation_id = getattr(locals().get('inventory_event'), 'correlation_id', 'unknown')
        
        logger.error(
            "Azure Function execution failed",
            correlation_id=correlation_id,
            error=str(e),
            error_type=type(e).__name__,
            message_id=msg.id,
            insertion_time=str(msg.insertion_time)
        )
        
        # Re-raise to mark function as failed
        raise


@app.route(route="health", auth_level=func.AuthLevel.ANONYMOUS)
def health_check(req: func.HttpRequest) -> func.HttpResponse:
    """Health check endpoint for the Azure Function"""
    
    health_status = {
        "service": "Inventory Event Processor",
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "configuration": {
            "supplier_api_url": SUPPLIER_API_URL,
            "retry_attempts": RETRY_ATTEMPTS,
            "timeout_seconds": TIMEOUT_SECONDS
        }
    }
    
    logger.info("Health check requested", status="healthy")
    
    return func.HttpResponse(
        json.dumps(health_status),
        status_code=200,
        mimetype="application/json"
    )


@app.route(route="test", auth_level=func.AuthLevel.ANONYMOUS)
def test_function(req: func.HttpRequest) -> func.HttpResponse:
    """Test endpoint to simulate event processing"""
    
    try:
        # Create a test inventory event
        test_event = {
            "event_id": "test-event-001",
            "correlation_id": "test-correlation-001",
            "event_type": "stock_below_threshold",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "product_id": "prod-001",
            "product_name": "Test Product",
            "current_stock": 2,
            "threshold": 10,
            "supplier_id": "supp-001",
            "suggested_order_quantity": 20
        }
        
        logger.info("Test function called", test_event=test_event)
        
        # Validate the test event
        inventory_event = InventoryEvent(**test_event)
        
        # Create supplier order
        supplier_order = SupplierOrderRequest(
            product_id=inventory_event.product_id,
            product_name=inventory_event.product_name,
            quantity=inventory_event.suggested_order_quantity,
            supplier_id=inventory_event.supplier_id,
            priority="normal",
            correlation_id=inventory_event.correlation_id
        )
        
        # Test Supplier API call
        try:
            supplier_response = call_supplier_api(supplier_order, inventory_event.correlation_id)
            
            response_data = {
                "status": "success",
                "message": "Test event processed successfully",
                "test_event": test_event,
                "supplier_response": supplier_response,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            
            return func.HttpResponse(
                json.dumps(response_data),
                status_code=200,
                mimetype="application/json"
            )
            
        except Exception as e:
            error_response = {
                "status": "error",
                "message": "Failed to process test event",
                "error": str(e),
                "test_event": test_event,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            
            return func.HttpResponse(
                json.dumps(error_response),
                status_code=500,
                mimetype="application/json"
            )
            
    except Exception as e:
        logger.error("Test function failed", error=str(e))
        
        error_response = {
            "status": "error",
            "message": "Test function execution failed",
            "error": str(e),
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
        return func.HttpResponse(
            json.dumps(error_response),
            status_code=500,
            mimetype="application/json"
        )
