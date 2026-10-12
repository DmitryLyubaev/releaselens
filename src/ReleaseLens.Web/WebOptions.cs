using Microsoft.Extensions.Configuration;

namespace ReleaseLens.Web;

public sealed class WebOptions
{
    public Uri GatewayBaseUrl { get; private init; } = new("https://invalid.example.test/");
    public string Model { get; private init; } = "";
    public string TenantId { get; private init; } = "";
    public string GatewayAudience { get; private init; } = "";
    public string OwnerObjectId { get; private init; } = "";
    public string DeployObjectId { get; private init; } = "";
    public string Slot { get; private init; } = "";
    public bool IsValid { get; private init; }

    public static WebOptions From(IConfiguration configuration)
    {
        string Get(string key) => configuration[$"Web:{key}"] ?? "";
        var uriValid = Uri.TryCreate(Get("GatewayBaseUrl"), UriKind.Absolute, out var uri)
            && uri.Scheme == "https" && uri.UserInfo.Length == 0 && uri.Query.Length == 0 && uri.Fragment.Length == 0;
        var model = Get("Model");
        var tenant = Get("TenantId");
        var owner = Get("OwnerObjectId");
        var deploy = Get("DeployObjectId");
        var audience = Get("GatewayAudience");
        var slot = Get("Slot");
        var valid = uriValid && model.Length is > 0 and <= 128 && !model.Any(char.IsControl)
            && Guid.TryParse(tenant, out _) && Guid.TryParse(owner, out _)
            && Guid.TryParse(deploy, out _) && owner != deploy
            && (Guid.TryParse(audience, out _) || (audience.StartsWith("api://", StringComparison.Ordinal) && Guid.TryParse(audience[6..], out _)))
            && slot is "main" or "staging";
        return new WebOptions
        {
            GatewayBaseUrl = uriValid ? uri! : new Uri("https://invalid.example.test/"),
            Model = model, TenantId = tenant, GatewayAudience = audience,
            OwnerObjectId = owner, DeployObjectId = deploy, Slot = valid ? slot : "unknown", IsValid = valid
        };
    }
    public override string ToString() => "WebOptions (private configuration)";
}
