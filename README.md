ShopStream Supplier Sync — README Additions
Event Service Choice and Rationale

This project uses Azure Storage Queue as the event broker for the inventory-events queue. The ShopStream backend acts as the producer: when product stock drops below the configured STOCK_THRESHOLD, it publishes a low-stock inventory event. The Azure Function acts as the consumer: it is triggered by new messages in the queue, validates the event, builds a supplier order request, and calls the Supplier API.

Azure Storage Queue was chosen because it is simple, low-cost, directly supported by Azure Functions queue triggers, and sufficient for this project’s one-producer/one-consumer workflow. The design does not require complex topics, subscriptions, sessions, or routing rules. Storage Queue also buffers messages when the Function is stopped or temporarily unavailable, which improves reliability compared with calling the Supplier API directly from the backend.

The main tradeoff is that Azure Storage Queue provides at-least-once delivery, not exactly-once delivery. This means a message may be delivered more than once if the function fails after performing the supplier-side effect but before the message is successfully completed. For this lab, that risk is documented. In production, the consumer should use event_id as an idempotency key in a durable store such as Cosmos DB or Redis before creating a supplier order.

Exact JSON Event Schema

The backend emits the following JSON message to the inventory-events queue:

{
  "event_id": "uuid4 string",
  "correlation_id": "uuid4 string",
  "event_type": "stock_below_threshold",
  "timestamp": "ISO 8601 UTC timestamp",
  "product_id": "string",
  "product_name": "string",
  "current_stock": 0,
  "threshold": 10,
  "supplier_id": "string",
  "suggested_order_quantity": 0
}

Formal schema:

{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "InventoryEvent",
  "type": "object",
  "required": [
    "event_id",
    "correlation_id",
    "event_type",
    "timestamp",
    "product_id",
    "product_name",
    "current_stock",
    "threshold",
    "supplier_id",
    "suggested_order_quantity"
  ],
  "properties": {
    "event_id": {
      "type": "string",
      "format": "uuid",
      "description": "Unique event identifier used for deduplication."
    },
    "correlation_id": {
      "type": "string",
      "format": "uuid",
      "description": "Trace identifier propagated from backend to queue to function to supplier API."
    },
    "event_type": {
      "type": "string",
      "const": "stock_below_threshold"
    },
    "timestamp": {
      "type": "string",
      "format": "date-time",
      "description": "UTC timestamp when the event was emitted."
    },
    "product_id": {
      "type": "string"
    },
    "product_name": {
      "type": "string"
    },
    "current_stock": {
      "type": "integer",
      "minimum": 0
    },
    "threshold": {
      "type": "integer",
      "minimum": 0
    },
    "supplier_id": {
      "type": "string"
    },
    "suggested_order_quantity": {
      "type": "integer",
      "minimum": 1
    }
  },
  "additionalProperties": false
}

Example event:

{
  "event_id": "REPLACE-WITH-REAL-EVENT-ID",
  "correlation_id": "REPLACE-WITH-REAL-CORRELATION-ID",
  "event_type": "stock_below_threshold",
  "timestamp": "2026-08-13T20:51:00Z",
  "product_id": "prod-001",
  "product_name": "Wireless Headphones",
  "current_stock": 4,
  "threshold": 10,
  "supplier_id": "supp-001",
  "suggested_order_quantity": 20
}
Local Deployment and Test Commands

Run these commands from the project root.

1. Verify tools
python --version
docker version
docker compose version
az version --query '"azure-cli"'
git --version
func --version
jq --version
2. Create the local .env file for Azurite
cp .env.example .env

For local Docker Compose testing, the backend container must use the Azurite hostname, not 127.0.0.1, because the backend runs inside the Docker network:

AZURE_STORAGE_CONNECTION_STRING=DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;BlobEndpoint=http://azurite:10000/devstoreaccount1;QueueEndpoint=http://azurite:10001/devstoreaccount1;TableEndpoint=http://azurite:10002/devstoreaccount1;
STOCK_THRESHOLD=10
SUPPLIER_API_URL=http://supplier-api:8001
BACKEND_API_URL=http://backend:8000
RETRY_ATTEMPTS=3
TIMEOUT_SECONDS=30
LOG_LEVEL=INFO
3. Start the local Docker Compose stack
docker compose up --build -d
docker compose ps

Expected result: backend, supplier-api, azurite, and supporting containers should show as running or healthy.

4. Test backend low-stock event emission
curl -s -X POST "http://localhost:8000/products/prod-001/simulate-sale?quantity=1" | tee /tmp/sale.json | jq .
export LOCAL_CID=$(cat /tmp/sale.json | jq -r .correlation_id)
echo "$LOCAL_CID"
docker compose logs backend --tail=60 | grep "$LOCAL_CID"
curl -s http://localhost:8000/queue/status | jq .

Expected result: the response should show below_threshold: true, and the backend logs should include Sale simulated and Inventory event emitted with the same correlation_id.

5. Test Supplier API directly
curl -s http://localhost:8001/ | jq .
curl -s http://localhost:8001/info | jq .

Test X-Correlation-ID header precedence:

export HDR_CID=$(python -c "import uuid; print(uuid.uuid4())")
curl -s -X POST http://localhost:8001/order \
  -H "Content-Type: application/json" \
  -H "X-Correlation-ID: $HDR_CID" \
  -d '{"product_id":"prod-001","product_name":"Wireless Headphones","quantity":20,"supplier_id":"supp-001","priority":"normal","correlation_id":"BODY-VALUE-MUST-LOSE"}' | jq .
docker compose logs supplier-api --tail=30 | grep "$HDR_CID"

Expected result: the response and logs should use the header value from X-Correlation-ID, not the body value.

Azure Deployment and Test Commands

Run these commands from Git Bash or a Bash-compatible terminal.

1. Set variables
export MSYS_NO_PATHCONV=1
export RESOURCE_GROUP="Student-RG-REPLACE-WITH-YOUR-ID"
export MYIP="$(curl -s https://ifconfig.me)"
az group show --name "$RESOURCE_GROUP" -o table
2. Provision Azure resources
bash scripts/setup-azure.sh 2>&1 | tee logs-setup-azure.txt
source .azure-config
cat .azure-config

The script provisions or configures the Azure resources needed for the final project, including the storage account, inventory-events queue, Function App, Supplier VM, Supplier subnet/NSG, Log Analytics workspace, and Application Insights.

3. Replace local Azurite connection string with real Azure Storage connection string
source .azure-config
AZ_CS=$(az storage account show-connection-string \
  --name "$STORAGE_ACCOUNT" \
  --resource-group "$RESOURCE_GROUP" \
  --query connectionString \
  -o tsv)

python - "$AZ_CS" <<'PY'
import io
import re
import sys

connection_string = sys.argv[1]
path = ".env"
text = io.open(path, encoding="utf-8").read()
text = re.sub(
    r"^AZURE_STORAGE_CONNECTION_STRING=.*$",
    "AZURE_STORAGE_CONNECTION_STRING=" + connection_string,
    text,
    count=1,
    flags=re.M
)
io.open(path, "w", encoding="utf-8", newline="\n").write(text)
print(".env now points at the real Azure Storage Queue")
PY
4. Deploy backend, supplier-api, and Azure Function
bash scripts/deploy-azure.sh 2>&1 | tee logs-deploy-azure.txt
source .azure-config
5. Verify deployed services
export VM_IP=$(az vm show \
  -g "$RESOURCE_GROUP" \
  -n "$VM_NAME" \
  --show-details \
  --query publicIps \
  -o tsv)

curl -s "http://$VM_IP:8000/" | jq .
curl -s "http://$VM_IP:8001/" | jq .
curl -s "https://$FUNCTION_APP.azurewebsites.net/api/health" | jq .

Expected result: the backend and supplier API should return JSON health output, and the Azure Function health endpoint should respond successfully.

6. Run the end-to-end supplier-sync test
curl -s -X POST "http://$VM_IP:8000/products/prod-001/simulate-sale?quantity=1" | tee /tmp/e2e.json | jq .
export CID=$(cat /tmp/e2e.json | jq -r .correlation_id)
echo "TRACE CORRELATION ID = $CID"

Check backend logs:

ssh -o StrictHostKeyChecking=no "$VM_USER@$VM_IP" \
  "cd ~/shopstream-supplier && sudo docker compose logs backend | grep $CID"

Check supplier logs:

ssh -o StrictHostKeyChecking=no "$VM_USER@$VM_IP" \
  "cd ~/shopstream-supplier && sudo docker compose logs supplier-api | grep $CID"

Check Function-side trace in Log Analytics:

let cid = "REPLACE-WITH-YOUR-REAL-CORRELATION-ID";
union isfuzzy=true withsource=SourceTable AppTraces, AppRequests, AppDependencies, AppExceptions
| where TimeGenerated > ago(2h)
| where Message has cid
    or tostring(Properties.correlation_id) == cid
    or tostring(Properties) has cid
| project TimeGenerated, SourceTable, Message, OperationId, Properties
| order by TimeGenerated asc
Sample Four-Line Correlation-ID Trace

Replace the example values below with your real lines from backend logs, supplier logs, Function logs, and KQL output. Do not submit fabricated correlation IDs.

Correlation ID traced: REPLACE-WITH-YOUR-REAL-CORRELATION-ID

1. backend      | INFO | Sale simulated | correlation_id=REPLACE-WITH-YOUR-REAL-CORRELATION-ID | product_id=prod-001 | quantity=1 | below_threshold=true
2. backend      | INFO | Inventory event emitted | correlation_id=REPLACE-WITH-YOUR-REAL-CORRELATION-ID | queue_name=inventory-events | event_type=stock_below_threshold
3. function     | INFO | Supplier API call successful | correlation_id=REPLACE-WITH-YOUR-REAL-CORRELATION-ID | supplier_id=supp-001 | product_id=prod-001
4. supplier-api | INFO | Order processed successfully | correlation_id=REPLACE-WITH-YOUR-REAL-CORRELATION-ID | order_id=REPLACE-WITH-REAL-ORDER-ID | confirmation_number=REPLACE-WITH-REAL-CONFIRMATION

This trace proves that the same correlation_id was generated at the backend entry point, written into the queue event, extracted by the Azure Function, passed to the Supplier API through the X-Correlation-ID header and body, and returned in the supplier confirmation.

At-Least-Once Delivery and Duplicate-Order Risk

Azure Storage Queue follows an at-least-once delivery model. This is useful because it reduces the chance of losing a low-stock event: if the Azure Function fails while processing a message, the message can become visible again and be retried. However, this also creates a duplicate-processing risk. For example, the Function could successfully call POST /order on the Supplier API, then crash before the queue message is fully completed. When the message is delivered again, the Function may create a second supplier order for the same low-stock event.

The correct production mitigation is idempotency. Each inventory event already has an event_id, so the Function should check a durable deduplication store before creating a supplier order. A production design could store processed event_id values in Cosmos DB or Redis with a TTL. If the same event_id appears again, the Function should log it as a duplicate, skip the supplier order side effect, and allow the queue message to complete.