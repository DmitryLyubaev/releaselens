using System.Text;
using System.Text.Json;
using Microsoft.AspNetCore.Http;
using Microsoft.Extensions.Configuration;
using ReleaseLens.Web;

namespace ReleaseLens.Web.Tests;

internal static class Fixtures
{
    public const string Owner = "00000000-0000-0000-0000-000000000001";
    public const string Deploy = "00000000-0000-0000-0000-000000000002";
    public const string Tenant = "00000000-0000-0000-0000-000000000003";
    public const string Audience = "api://00000000-0000-0000-0000-000000000004";
    public static Dictionary<string, string?> Configuration() => new()
    {
        ["Web:GatewayBaseUrl"] = "https://gateway.example.test/azure-openai/v1/",
        ["Web:Model"] = "test-model", ["Web:TenantId"] = Tenant,
        ["Web:GatewayAudience"] = Audience, ["Web:OwnerObjectId"] = Owner,
        ["Web:DeployObjectId"] = Deploy, ["Web:Slot"] = "main"
    };
    public static WebOptions Options() => WebOptions.From(new Microsoft.Extensions.Configuration.ConfigurationBuilder().AddInMemoryCollection(Configuration()).Build());
    public static string Claims(params (string Type, string Value)[] claims) => Convert.ToBase64String(Encoding.UTF8.GetBytes(JsonSerializer.Serialize(new
    {
        auth_typ = "aad", claims = claims.Select(c => new { typ = c.Type, val = c.Value }).ToArray()
    })));
    public static string Principal(CallerKind caller) => Claims(("oid", caller == CallerKind.Owner ? Owner : Deploy), ("roles", caller == CallerKind.Owner ? "WebApp.Use" : "WebApp.Smoke"));
    public static IHeaderDictionary Headers(string? principal)
    {
        var headers = new HeaderDictionary();
        if (principal is not null) headers["X-MS-CLIENT-PRINCIPAL"] = principal;
        return headers;
    }
    public static string GatewayToken(DateTimeOffset expiry) => Token(new { tid = Tenant, oid = Owner, aud = Audience, scp = "access_as_user", roles = new[] { "Gateway.Invoke" }, exp = expiry.ToUnixTimeSeconds() });
    public static string Token(object claims)
    {
        static string Encode(object value) => Convert.ToBase64String(JsonSerializer.SerializeToUtf8Bytes(value)).TrimEnd('=').Replace('+', '-').Replace('/', '_');
        return $"{Encode(new { alg = "none" })}.{Encode(claims)}.test";
    }
}
