#!/bin/bash

# ShopStream Supplier Sync - Azure Setup Script
# Milestone 3 (Final Project). This EXTENDS the ShopStream environment you built
# in Milestone 1 (VNet + Web/App/DB subnets + NSGs) and monitored in Milestone 2
# (VNet Flow Logs + Network Watcher). It adds one hardened Supplier-Subnet to your
# existing ShopStream-VNet and deploys the supplier-sync services into it.

set -e

# Project root (same convention as deploy-azure.sh) — .azure-config is written here.
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Configuration
# RESOURCE_GROUP must be your pre-assigned RG (Student-RG-<your-ODL-id>).
# Auto-detect Student-RG-* if not set explicitly.
if [ -z "${RESOURCE_GROUP:-}" ]; then
    RESOURCE_GROUP="$(az group list --query "[?starts_with(name,'Student-RG-')].name | [0]" -o tsv 2>/dev/null)"
fi
: "${RESOURCE_GROUP:?Set RESOURCE_GROUP=Student-RG-<your-ODL-id> before running this script}"
LOCATION="canadacentral"

# --- Milestone 1 / Milestone 2 continuation ---
# M3 does NOT create a fresh network. It reuses the ShopStream-VNet you created in
# Milestone 1 and adds one new subnet (10.0.5.0/24) for the supplier-sync services,
# so the whole M1 -> M2 -> M3 build lives in one virtual network.
VNET_NAME="ShopStream-VNet"
SUBNET_NAME="Supplier-Subnet"
SUBNET_PREFIX="10.0.5.0/24"

# Names that must be globally unique across all of Azure get a per-run suffix
# so that multiple students do not collide on the same name.
# Storage accounts are capped at 24 lowercase-alnum chars; 'shopstreamsync' is 14,
# so 'shopstreamsync' + 6 epoch digits = 20 (within the limit).
UNIQ="$(date +%s | tail -c 7)"
STORAGE_ACCOUNT="shopstreamsync${UNIQ}"
FUNCTION_APP="shopstream-supplier-functions-${UNIQ}"
VM_NAME="shopstream-supplier-vm"
VM_DNS_LABEL="shopstream-supplier-vm-${UNIQ}"        # globally-unique DNS label
VM_SIZE="Standard_B2s"
VM_USER="azureuser"
MYIP="$(curl -s https://ifconfig.me)/32"   # restrict SSH to your current IP (M1/CK4 pattern)

echo "🚀 Setting up ShopStream Supplier Sync in Azure..."
echo "Resource Group: $RESOURCE_GROUP"
echo "Location: $LOCATION"
echo "Storage Account: $STORAGE_ACCOUNT"

# Check if Azure CLI is installed
if ! command -v az &> /dev/null; then
    echo "❌ Azure CLI is not installed. Please install it first."
    echo "Visit: https://learn.microsoft.com/cli/azure/install-azure-cli"
    exit 1
fi

# Login to Azure
echo "🔐 Logging into Azure..."
az login

# Verify the pre-assigned RG exists and is accessible:
echo "📦 Verifying pre-assigned resource group..."
az group show --name "$RESOURCE_GROUP" -o table

# --- Prerequisite: the ShopStream VNet from Milestone 1 must exist ---
echo "🔎 Verifying the ShopStream network from Milestone 1..."
if ! az network vnet show -g "$RESOURCE_GROUP" -n "$VNET_NAME" -o none 2>/dev/null; then
    echo "❌ '$VNET_NAME' was not found in $RESOURCE_GROUP."
    echo "   Milestone 3 continues the network you built in Milestone 1. If you have"
    echo "   torn it down, rebuild the ShopStream baseline first with the Milestone 2"
    echo "   starter:  bash setup_infrastructure.sh   (from the Milestone 2 folder)"
    exit 1
fi
echo "   ✅ Found $VNET_NAME — the supplier-sync services will deploy into it."

# Create Storage Account
echo "💾 Creating storage account..."
az storage account create \
    --name $STORAGE_ACCOUNT \
    --resource-group $RESOURCE_GROUP \
    --location $LOCATION \
    --sku Standard_LRS \
    --kind StorageV2

# Get storage account connection string
echo "🔑 Getting storage account connection string..."
STORAGE_CONNECTION_STRING=$(az storage account show-connection-string \
    --name $STORAGE_ACCOUNT \
    --resource-group $RESOURCE_GROUP \
    --query connectionString \
    --output tsv)

# Create queue
echo "📬 Creating inventory events queue..."
az storage queue create \
    --name "inventory-events" \
    --connection-string "$STORAGE_CONNECTION_STRING"

# Create Function App
echo "⚡ Creating Azure Function App..."
az functionapp create \
    --resource-group $RESOURCE_GROUP \
    --consumption-plan-location $LOCATION \
    --runtime python \
    --runtime-version 3.11 \
    --functions-version 4 \
    --os-type Linux \
    --name $FUNCTION_APP \
    --storage-account $STORAGE_ACCOUNT

# Configure Function App settings
echo "⚙️ Configuring Function App settings..."
az functionapp config appsettings set \
    --name $FUNCTION_APP \
    --resource-group $RESOURCE_GROUP \
    --settings \
        "AzureWebJobsStorage=$STORAGE_CONNECTION_STRING" \
        "SUPPLIER_API_URL=http://${VM_DNS_LABEL}.${LOCATION}.cloudapp.azure.com:8001" \
        "RETRY_ATTEMPTS=3" \
        "TIMEOUT_SECONDS=30"

# --- Networking: add a hardened Supplier-Subnet to the ShopStream VNet ---
echo "🖥️ Adding the Supplier-Subnet + services to $VNET_NAME..."

# Create public IP with a globally-unique DNS label (timestamped above)
az network public-ip create \
    --resource-group $RESOURCE_GROUP \
    --name "${VM_NAME}-ip" \
    --dns-name "$VM_DNS_LABEL"

# Create a Network Security Group for the supplier subnet. It is associated at the
# SUBNET level (like the Web/App/DB NSGs in Milestone 1), so every NIC placed in
# Supplier-Subnet inherits it.
az network nsg create \
    --resource-group $RESOURCE_GROUP \
    --name "${VM_NAME}-nsg"

# Allow SSH from your current IP only (M1/CK4 hardening pattern)
az network nsg rule create \
    --resource-group $RESOURCE_GROUP \
    --nsg-name "${VM_NAME}-nsg" \
    --name "SSH" \
    --protocol tcp \
    --priority 1000 \
    --destination-port-range 22 \
    --source-address-prefix "$MYIP" \
    --access allow

# Allow backend API (port 8000) — used for your own test traffic
az network nsg rule create \
    --resource-group $RESOURCE_GROUP \
    --nsg-name "${VM_NAME}-nsg" \
    --name "Backend-API" \
    --protocol tcp \
    --priority 1001 \
    --destination-port-range 8000 \
    --access allow

# Allow supplier API (port 8001) — the Azure Function (serverless, dynamic egress
# IP) posts orders here over the VM's public FQDN, so this must accept inbound.
az network nsg rule create \
    --resource-group $RESOURCE_GROUP \
    --nsg-name "${VM_NAME}-nsg" \
    --name "Supplier-API" \
    --protocol tcp \
    --priority 1002 \
    --destination-port-range 8001 \
    --access allow

# Add the new subnet to the EXISTING ShopStream VNet and attach the NSG to it.
az network vnet subnet create \
    --resource-group $RESOURCE_GROUP \
    --vnet-name "$VNET_NAME" \
    --name "$SUBNET_NAME" \
    --address-prefix "$SUBNET_PREFIX" \
    --network-security-group "${VM_NAME}-nsg"

# Create the VM inside ShopStream-VNet / Supplier-Subnet. The NSG is on the subnet
# (M1 pattern), so the VM is created with no NIC-level NSG (--nsg "").
az vm create \
    --resource-group $RESOURCE_GROUP \
    --name $VM_NAME \
    --image Ubuntu2204 \
    --size $VM_SIZE \
    --storage-sku Standard_LRS \
    --admin-username $VM_USER \
    --generate-ssh-keys \
    --os-disk-delete-option Delete \
    --public-ip-address "${VM_NAME}-ip" \
    --nsg "" \
    --vnet-name "$VNET_NAME" \
    --subnet "$SUBNET_NAME"

# Install Docker on VM
echo "🐳 Installing Docker on VM..."
az vm run-command invoke \
    --resource-group $RESOURCE_GROUP \
    --name $VM_NAME \
    --command-id RunShellScript \
    --scripts "
        sudo apt-get update
        # docker.io is the OS package for Docker Engine. Ubuntu 22.04's universe
        # repo ships docker-compose-v2 which provides 'docker compose' (with a
        # space) — the supported v2 syntax. The legacy 'docker-compose' (v1,
        # hyphenated) package is unmaintained and not used.
        sudo apt-get install -y docker.io docker-compose-v2
        sudo systemctl start docker
        sudo systemctl enable docker
        sudo usermod -aG docker $VM_USER
        docker --version
        docker compose version
    "

# Create Log Analytics Workspace
echo "📊 Creating Log Analytics workspace..."
LOG_WORKSPACE="shopstream-supplier-logs"
az monitor log-analytics workspace create \
    --resource-group $RESOURCE_GROUP \
    --workspace-name $LOG_WORKSPACE \
    --location $LOCATION

# Get workspace ID and key
# Use the workspace ARM resource ID (not the customerId GUID) so App Insights
# can link to it. customerId is the OMS connection identifier and is rejected by
# 'az monitor app-insights component create --workspace'.
WORKSPACE_ID=$(az monitor log-analytics workspace show \
    --resource-group $RESOURCE_GROUP \
    --workspace-name $LOG_WORKSPACE \
    --query id \
    --output tsv)

WORKSPACE_KEY=$(az monitor log-analytics workspace get-shared-keys \
    --resource-group $RESOURCE_GROUP \
    --workspace-name $LOG_WORKSPACE \
    --query primarySharedKey \
    --output tsv)

# Create Application Insights
echo "📈 Creating Application Insights..."
APP_INSIGHTS="shopstream-supplier-insights"
az extension add --name application-insights
az monitor app-insights component create \
    --app $APP_INSIGHTS \
    --location $LOCATION \
    --resource-group $RESOURCE_GROUP \
    --workspace $WORKSPACE_ID

# Link the Function App to Application Insights so its structured logs and
# correlation IDs flow into Log Analytics for the Task 4 end-to-end trace.
AI_CONNECTION_STRING=$(az monitor app-insights component show \
    --app $APP_INSIGHTS \
    --resource-group $RESOURCE_GROUP \
    --query connectionString --output tsv)
az functionapp config appsettings set \
    --name $FUNCTION_APP \
    --resource-group $RESOURCE_GROUP \
    --settings "APPLICATIONINSIGHTS_CONNECTION_STRING=$AI_CONNECTION_STRING"

# Get VM public IP
VM_PUBLIC_IP=$(az vm show \
    --resource-group $RESOURCE_GROUP \
    --name $VM_NAME \
    --show-details \
    --query publicIps \
    --output tsv)

# Persist resolved names so deploy-azure.sh and other scripts can pick them up.
# Written to the project root — the same path deploy-azure.sh reads.
cat > "$PROJECT_ROOT/.azure-config" <<EOF
RESOURCE_GROUP="$RESOURCE_GROUP"
LOCATION="$LOCATION"
VNET_NAME="$VNET_NAME"
SUBNET_NAME="$SUBNET_NAME"
STORAGE_ACCOUNT="$STORAGE_ACCOUNT"
FUNCTION_APP="$FUNCTION_APP"
VM_NAME="$VM_NAME"
VM_DNS_LABEL="$VM_DNS_LABEL"
VM_USER="$VM_USER"
EOF
echo "(Wrote $PROJECT_ROOT/.azure-config — deploy-azure.sh will source it automatically.)"

# Output configuration
echo ""
echo "✅ Azure setup completed successfully!"
echo ""
echo "📋 Configuration Summary:"
echo "========================"
echo "Resource Group: $RESOURCE_GROUP"
echo "ShopStream VNet: $VNET_NAME  (new subnet: $SUBNET_NAME $SUBNET_PREFIX)"
echo "Storage Account: $STORAGE_ACCOUNT"
echo "Function App: $FUNCTION_APP"
echo "VM Name: $VM_NAME"
echo "VM Public IP: $VM_PUBLIC_IP"
echo "VM SSH: ssh $VM_USER@$VM_PUBLIC_IP"
echo ""
echo "🔧 Next Steps:"
echo "1. Copy the storage connection string to your .env file:"
echo "   AZURE_STORAGE_CONNECTION_STRING=\"$STORAGE_CONNECTION_STRING\""
echo ""
echo "2. Deploy your applications:"
echo "   ./scripts/deploy-azure.sh"
echo ""
echo "3. Test the system:"
echo "   curl http://$VM_PUBLIC_IP:8000/"
echo "   curl http://$VM_PUBLIC_IP:8001/"
echo ""
echo "4. Task 4 (security posture) — reuse your Milestone 2 monitoring on this VNet:"
echo "   enable VNet Flow Logs on $VNET_NAME and review the Supplier-Subnet NSG rules."
echo ""
echo "🔗 Azure Portal URLs:"
echo "Function App: https://portal.azure.com/#@/resource/subscriptions/$(az account show --query id -o tsv)/resourceGroups/$RESOURCE_GROUP/providers/Microsoft.Web/sites/$FUNCTION_APP"
echo "Storage Account: https://portal.azure.com/#@/resource/subscriptions/$(az account show --query id -o tsv)/resourceGroups/$RESOURCE_GROUP/providers/Microsoft.Storage/storageAccounts/$STORAGE_ACCOUNT"
echo "Virtual Machine: https://portal.azure.com/#@/resource/subscriptions/$(az account show --query id -o tsv)/resourceGroups/$RESOURCE_GROUP/providers/Microsoft.Compute/virtualMachines/$VM_NAME"
