# Terraform state storage, with one container per stack. With shared keys and local users
# off, every data-plane request has to be authorised through Entra ID.
resource "azurerm_storage_account" "state" {
  name                     = "strlstate${random_string.suffix.result}"
  resource_group_name      = azurerm_resource_group.bootstrap.name
  location                 = azurerm_resource_group.bootstrap.location
  account_tier             = "Standard"
  account_replication_type = "LRS"

  shared_access_key_enabled       = false
  default_to_oauth_authentication = true
  # azurerm 5.x still defaults this to true.
  local_user_enabled = false
  # 5.x defaults this to false and 4.x defaulted it to true, so it is written out rather
  # than left to the provider default.
  allow_nested_items_to_be_public = false
  min_tls_version                 = "TLS1_2"

  # A damaged or deleted state can be recovered from an earlier blob version or from soft
  # delete.
  blob_properties {
    versioning_enabled = true

    delete_retention_policy {
      days = 7
    }

    container_delete_retention_policy {
      days = 7
    }
  }

  lifecycle {
    prevent_destroy = true
  }
}

resource "azurerm_storage_container" "bootstrap" {
  name                  = "tfstate-bootstrap"
  storage_account_id    = azurerm_storage_account.state.id
  container_access_type = "private"
}

resource "azurerm_storage_container" "app" {
  name                  = "tfstate-app"
  storage_account_id    = azurerm_storage_account.state.id
  container_access_type = "private"
}

# Every version is a full copy of a state file, and state can hold secrets, so old versions
# are not kept indefinitely. Version actions never touch the current blob.
resource "azurerm_storage_management_policy" "state" {
  storage_account_id = azurerm_storage_account.state.id

  rule {
    name    = "deleteOldVersions"
    enabled = true

    filters {
      blob_types = ["blockBlob"]
    }

    actions {
      version {
        delete_after_days_since_creation = 90
      }
    }
  }
}
