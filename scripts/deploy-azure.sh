#!/bin/bash

# ShopStream Supplier Sync - Azure Deployment Script

set -e

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Configuration: prefer the .azure-config written by setup-azure.sh
# (which has the timestamped FUNCTION_APP / VM_DNS_LABEL names).
# Falls back to defaults only if .azure-config is missing.
if [ -f "$PROJECT_ROOT/.azure-config" ]; then
    # shellcheck source=/dev/null
    . "$PROJECT_ROOT/.azure-config"
    echo "Loaded resolved names from .azure-config (FUNCTION_APP=$FUNCTION_APP)"
else
    echo "⚠️  No .azure-config found. Run ./scripts/setup-azure.sh first, or export"
    echo "    RESOURCE_GROUP / FUNCTION_APP / VM_NAME / VM_USER manually before running."
    if [ -z "${RESOURCE_GROUP:-}" ]; then
        # Auto-detect: pick the first Student-RG-* the user has access to.
        RESOURCE_GROUP="$(az group list --query "[?starts_with(name,'Student-RG-')].name | [0]" -o tsv 2>/dev/null)"
    fi
    : "${RESOURCE_GROUP:?Set RESOURCE_GROUP=Student-RG-<your-ODL-id> before running this script}"
    FUNCTION_APP="${FUNCTION_APP:-shopstream-supplier-functions}"
    VM_NAME="${VM_NAME:-shopstream-supplier-vm}"
    VM_USER="${VM_USER:-azureuser}"
fi

echo "🚀 Deploying ShopStream Supplier Sync to Azure..."
echo "Project Root: $PROJECT_ROOT"

# Check if .env file exists
if [ ! -f "$PROJECT_ROOT/.env" ]; then
    echo "⚠️ .env file not found. Creating from .env.example..."
    cp "$PROJECT_ROOT/.env.example" "$PROJECT_ROOT/.env"
    echo "📝 Please edit .env file with your Azure configuration before proceeding."
    exit 1
fi

# Load environment variables
source "$PROJECT_ROOT/.env"

# Get VM public IP
echo "🔍 Getting VM public IP..."
VM_PUBLIC_IP=$(az vm show \
    --resource-group $RESOURCE_GROUP \
    --name $VM_NAME \
    --show-details \
    --query publicIps \
    --output tsv)

echo "VM Public IP: $VM_PUBLIC_IP"

# Deploy Azure Function
echo "⚡ Deploying Azure Function..."
cd "$PROJECT_ROOT/azure-function"

# Install Azure Functions Core Tools if not installed.
# npm is the cross-platform path (works on macOS/Linux/WSL) and avoids the
# Homebrew tap-trust prompt that "brew tap azure/functions" now triggers on
# Homebrew 4.x. Requires Node.js (you already have it from CheckPoint 8).
if ! command -v func &> /dev/null; then
    echo "📦 Installing Azure Functions Core Tools (via npm)..."
    if command -v npm &> /dev/null; then
        npm install -g azure-functions-core-tools@4 --unsafe-perm true
    elif [[ "$OSTYPE" == "darwin"* ]]; then
        # Fallback for macOS without Node: trust the tap first, then install.
        brew tap azure/functions && brew install azure-functions-core-tools@4 \
            || { echo "If brew reports an untrusted tap, run: brew trust azure/functions"; exit 1; }
    else
        echo "Please install Azure Functions Core Tools manually:"
        echo "https://learn.microsoft.com/azure/azure-functions/functions-run-local"
        exit 1
    fi
fi

# Deploy function
func azure functionapp publish $FUNCTION_APP --python

echo "✅ Azure Function deployed successfully!"

# Deploy Docker services to VM
echo "🐳 Deploying Docker services to VM..."

# Create deployment directory on VM
ssh -o StrictHostKeyChecking=no $VM_USER@$VM_PUBLIC_IP "mkdir -p ~/shopstream-supplier"

# Copy project files to VM
echo "📤 Copying project files to VM..."
scp -o StrictHostKeyChecking=no -r "$PROJECT_ROOT/backend" $VM_USER@$VM_PUBLIC_IP:~/shopstream-supplier/
scp -o StrictHostKeyChecking=no -r "$PROJECT_ROOT/supplier-api" $VM_USER@$VM_PUBLIC_IP:~/shopstream-supplier/
scp -o StrictHostKeyChecking=no "$PROJECT_ROOT/docker-compose.yml" $VM_USER@$VM_PUBLIC_IP:~/shopstream-supplier/
scp -o StrictHostKeyChecking=no "$PROJECT_ROOT/.env" $VM_USER@$VM_PUBLIC_IP:~/shopstream-supplier/

# Deploy services on VM
echo "🚢 Starting services on VM..."
ssh -o StrictHostKeyChecking=no $VM_USER@$VM_PUBLIC_IP "
    cd ~/shopstream-supplier
    
    # Stop any existing services
    sudo docker compose down 2>/dev/null || true
    
    # Build and start services
    sudo docker compose up --build -d
    
    # Show status
    sudo docker compose ps
    
    # Show logs
    echo '📊 Service logs:'
    sudo docker compose logs --tail=20
"

# Test deployments
echo "🧪 Testing deployments..."

# Test backend API
echo "Testing backend API..."
if curl -f -s http://$VM_PUBLIC_IP:8000/ > /dev/null; then
    echo "✅ Backend API is responding"
else
    echo "❌ Backend API is not responding"
fi

# Test supplier API
echo "Testing supplier API..."
if curl -f -s http://$VM_PUBLIC_IP:8001/ > /dev/null; then
    echo "✅ Supplier API is responding"
else
    echo "❌ Supplier API is not responding"
fi

# Test Azure Function
echo "Testing Azure Function..."
FUNCTION_URL="https://$FUNCTION_APP.azurewebsites.net/api/health"
if curl -f -s $FUNCTION_URL > /dev/null; then
    echo "✅ Azure Function is responding"
else
    echo "❌ Azure Function is not responding"
fi

# Create test script on VM
echo "📝 Creating test script on VM..."
ssh -o StrictHostKeyChecking=no $VM_USER@$VM_PUBLIC_IP "cat > ~/shopstream-supplier/test-system.sh << 'EOF'
#!/bin/bash

echo '🧪 Testing ShopStream Supplier Sync System'
echo '=========================================='

# Test backend health
echo '1. Testing Backend API...'
curl -s http://localhost:8000/ | jq '.'

echo -e '\n2. Testing Supplier API...'
curl -s http://localhost:8001/ | jq '.'

echo -e '\n3. Getting current products...'
curl -s http://localhost:8000/products | jq '.'

echo -e '\n4. Simulating a sale (this should trigger an event)...'
curl -s -X POST http://localhost:8000/products/prod-001/simulate-sale?quantity=3 | jq '.'

echo -e '\n5. Checking queue status...'
curl -s http://localhost:8000/queue/status | jq '.'

echo -e '\n6. Getting supplier orders...'
curl -s http://localhost:8001/orders | jq '.'

echo -e '\nTest completed! Check Azure Function logs in the portal for event processing.'
EOF

chmod +x ~/shopstream-supplier/test-system.sh
"

echo ""
echo "✅ Deployment completed successfully!"
echo ""
echo "📋 Deployment Summary:"
echo "====================="
echo "Backend API: http://$VM_PUBLIC_IP:8000"
echo "Supplier API: http://$VM_PUBLIC_IP:8001"
echo "Azure Function: https://$FUNCTION_APP.azurewebsites.net"
echo ""
echo "🧪 To test the system:"
echo "1. SSH to VM: ssh $VM_USER@$VM_PUBLIC_IP"
echo "2. Run test script: ~/shopstream-supplier/test-system.sh"
echo ""
echo "📊 Monitoring:"
echo "- Docker logs: ssh $VM_USER@$VM_PUBLIC_IP 'cd ~/shopstream-supplier && sudo docker compose logs -f'"
echo "- Azure Function logs: https://portal.azure.com (search for $FUNCTION_APP)"
echo ""
echo "🔗 Azure Portal Links:"
echo "- Function App: https://portal.azure.com/#@/resource/subscriptions/$(az account show --query id -o tsv)/resourceGroups/$RESOURCE_GROUP/providers/Microsoft.Web/sites/$FUNCTION_APP"
echo "- Virtual Machine: https://portal.azure.com/#@/resource/subscriptions/$(az account show --query id -o tsv)/resourceGroups/$RESOURCE_GROUP/providers/Microsoft.Compute/virtualMachines/$VM_NAME"
