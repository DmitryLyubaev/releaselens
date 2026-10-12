using System.Text.Json;

namespace ReleaseLens.Web;

public static class EasyAuthPrincipalReader
{
    public static CallerKind Read(IHeaderDictionary headers, WebOptions options)
    {
        if (!headers.TryGetValue("X-MS-CLIENT-PRINCIPAL", out var values) || values.Count != 1 || values[0] is not { Length: > 0 and <= 32768 } encoded)
            return CallerKind.None;
        try
        {
            using var document = JsonDocument.Parse(Convert.FromBase64String(encoded));
            var root = document.RootElement;
            if (root.ValueKind != JsonValueKind.Object) return CallerKind.None;
            var properties = new HashSet<string>(StringComparer.Ordinal);
            foreach (var property in root.EnumerateObject())
                if (!properties.Add(property.Name)) return CallerKind.None;
            if (root.ValueKind != JsonValueKind.Object || !root.TryGetProperty("auth_typ", out var auth) || auth.GetString() != "aad"
                || !root.TryGetProperty("claims", out var claims) || claims.ValueKind != JsonValueKind.Array)
                return CallerKind.None;
            string? oid = null;
            var roles = new HashSet<string>(StringComparer.Ordinal);
            foreach (var claim in claims.EnumerateArray())
            {
                if (claim.ValueKind != JsonValueKind.Object || !claim.TryGetProperty("typ", out var type) || !claim.TryGetProperty("val", out var value)
                    || type.ValueKind != JsonValueKind.String || value.ValueKind != JsonValueKind.String)
                    return CallerKind.None;
                var kind = type.GetString();
                if (kind is "oid" or "http://schemas.microsoft.com/identity/claims/objectidentifier")
                {
                    if (oid is not null) return CallerKind.None;
                    oid = value.GetString();
                }
                else if (kind is "roles" or "http://schemas.microsoft.com/ws/2008/06/identity/claims/role")
                {
                    if (!roles.Add(value.GetString()!)) return CallerKind.None;
                }
            }
            if (roles.Contains("WebApp.Use") && roles.Contains("WebApp.Smoke")) return CallerKind.None;
            if (oid == options.OwnerObjectId && roles.Contains("WebApp.Use")) return CallerKind.Owner;
            if (oid == options.DeployObjectId && roles.Contains("WebApp.Smoke")) return CallerKind.Deployment;
        }
        catch (Exception ex) when (ex is FormatException or JsonException or InvalidOperationException) { }
        return CallerKind.None;
    }
}
