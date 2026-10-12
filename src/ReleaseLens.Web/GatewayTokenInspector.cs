using System.Text.Json;

namespace ReleaseLens.Web;

public static class GatewayTokenInspector
{
    // Sanity checks only. Easy Auth and the gateway own signature verification.
    public static SessionSnapshot Inspect(string? token, WebOptions options, DateTimeOffset now)
    {
        var present = !string.IsNullOrEmpty(token);
        var empty = new SessionSnapshot(present, false, false, false, false, false, false, false, null);
        if (!present || token!.Length > 32768) return empty;
        try
        {
            var parts = token.Split('.');
            if (parts.Length != 3 || parts.Any(p => p.Length == 0) || parts[1].Any(c => !char.IsAsciiLetterOrDigit(c) && c is not '-' and not '_')) return empty;
            var payload = parts[1].Replace('-', '+').Replace('_', '/');
            payload = payload.PadRight(payload.Length + (4 - payload.Length % 4) % 4, '=');
            using var document = JsonDocument.Parse(Convert.FromBase64String(payload));
            var root = document.RootElement;
            if (root.ValueKind != JsonValueKind.Object) return empty;
            var names = new HashSet<string>(StringComparer.Ordinal);
            if (root.EnumerateObject().Any(p => !names.Add(p.Name))) return empty;
            string? StringClaim(string key) => root.TryGetProperty(key, out var value) && value.ValueKind == JsonValueKind.String ? value.GetString() : null;
            var tenant = StringClaim("tid") == options.TenantId;
            var owner = StringClaim("oid") == options.OwnerObjectId;
            var audienceId = options.GatewayAudience.StartsWith("api://", StringComparison.Ordinal) ? options.GatewayAudience[6..] : options.GatewayAudience;
            var audience = StringClaim("aud") is { } aud && (aud == audienceId || aud == $"api://{audienceId}");
            var scope = StringClaim("scp")?.Split(' ', StringSplitOptions.RemoveEmptyEntries).Contains("access_as_user", StringComparer.Ordinal) == true;
            var role = root.TryGetProperty("roles", out var roles) && roles.ValueKind == JsonValueKind.Array
                && roles.EnumerateArray().All(r => r.ValueKind == JsonValueKind.String)
                && roles.EnumerateArray().Any(r => r.GetString() == "Gateway.Invoke");
            long? expiry = root.TryGetProperty("exp", out var exp) && exp.TryGetInt64(out var seconds) ? seconds : null;
            var usable = options.IsValid && tenant && owner && audience && scope && role && expiry > now.ToUnixTimeSeconds();
            return new SessionSnapshot(true, true, tenant, audience, owner, scope, role, usable, expiry);
        }
        catch (Exception ex) when (ex is FormatException or JsonException or InvalidOperationException) { return empty; }
    }
}
