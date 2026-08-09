# Infrastructure

Ephemeral by design. Deploy, demonstrate, destroy.

## Deploy

    cp terraform.tfvars.example terraform.tfvars   # then fill it in — it is git-ignored
    terraform init
    terraform apply -target=azurerm_resource_group.this -target=azurerm_consumption_budget_subscription.this
    terraform apply

The first `apply` is targeted so the **budget exists before any resource that can bill**.

## Tear down — do this at the end of every session

    terraform destroy -auto-approve

## On "hard spending cap"

Azure does **not** offer a hard spending cap on pay-as-you-go subscriptions. What this
configuration deploys is a budget with actual alerts at 50/80/100 percent and a forecast
alert, all emailing the configured address. That is alerting, not enforcement.

The control that actually prevents an unpleasant bill is destroying the resources when
you stop working. Scale-to-zero means an idle Container App costs nothing; the Burstable
B1ms Postgres server does not scale to zero and bills while it exists.
