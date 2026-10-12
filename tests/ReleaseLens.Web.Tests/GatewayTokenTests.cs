using ReleaseLens.Web;

namespace ReleaseLens.Web.Tests;

public sealed class GatewayTokenTests
{
    private static readonly DateTimeOffset Now = new(2026, 10, 12, 0, 0, 0, TimeSpan.Zero);
    public static Dictionary<string, object> Claims() => new()
    {
        ["tid"] = Fixtures.Tenant, ["oid"] = Fixtures.Owner, ["aud"] = Fixtures.Audience,
        ["scp"] = "access_as_user", ["roles"] = new[] { "Gateway.Invoke" }, ["exp"] = Now.AddMinutes(10).ToUnixTimeSeconds()
    };

    [Fact]
    public void ExpectedTokenIsUsableWithOnlySafeDiagnostics()
    {
        var snapshot = GatewayTokenInspector.Inspect(Fixtures.GatewayToken(Now.AddMinutes(10)), Fixtures.Options(), Now);
        Assert.True(snapshot.Usable);
        Assert.Equal(Now.AddMinutes(10).ToUnixTimeSeconds(), snapshot.ExpiresAtUnixSeconds);
        Assert.DoesNotContain(Fixtures.Owner, System.Text.Json.JsonSerializer.Serialize(snapshot));
    }

    [Theory]
    [InlineData("aud", "00000000-0000-0000-0000-000000000004")]
    [InlineData("scp", "other access_as_user another")]
    public void AudienceAliasAndSpaceDelimitedScopeAreAccepted(string key, string value)
    {
        var claims = Claims(); claims[key] = value;
        Assert.True(GatewayTokenInspector.Inspect(Fixtures.Token(claims), Fixtures.Options(), Now).Usable);
    }

    [Theory]
    [InlineData(null)]
    [InlineData("")]
    [InlineData("not-a-token")]
    [InlineData("e30.e30.test")]
    [InlineData("e30.W10.test")]
    public void MissingOrMalformedTokenIsUnusable(string? value) => Assert.False(GatewayTokenInspector.Inspect(value, Fixtures.Options(), Now).Usable);

    [Theory]
    [InlineData("tid", "wrong")]
    [InlineData("oid", "wrong")]
    [InlineData("aud", "wrong")]
    [InlineData("scp", "other")]
    [InlineData("roles", "other")]
    [InlineData("exp", "wrong")]
    public void WrongRequiredClaimIsUnusable(string key, string value)
    {
        var claims = Claims(); claims[key] = value;
        Assert.False(GatewayTokenInspector.Inspect(Fixtures.Token(claims), Fixtures.Options(), Now).Usable);
    }

    [Theory]
    [InlineData("tid")]
    [InlineData("oid")]
    [InlineData("aud")]
    [InlineData("scp")]
    [InlineData("roles")]
    [InlineData("exp")]
    public void MissingRequiredClaimIsUnusable(string key)
    {
        var claims = Claims(); claims.Remove(key);
        Assert.False(GatewayTokenInspector.Inspect(Fixtures.Token(claims), Fixtures.Options(), Now).Usable);
    }

    [Theory]
    [InlineData(-1)]
    [InlineData(0)]
    public void ExpiredTokenIsUnusable(int delta) => Assert.False(GatewayTokenInspector.Inspect(Fixtures.GatewayToken(Now.AddSeconds(delta)), Fixtures.Options(), Now).Usable);

    [Fact]
    public void DuplicateRequiredJsonClaimsAreDenied()
    {
        var payload = System.Text.Json.JsonSerializer.Serialize(Claims());
        payload = payload.Insert(1, "\"oid\":\"conflict\",");
        var encoded = Convert.ToBase64String(System.Text.Encoding.UTF8.GetBytes(payload)).TrimEnd('=').Replace('+', '-').Replace('/', '_');
        Assert.False(GatewayTokenInspector.Inspect($"e30.{encoded}.test", Fixtures.Options(), Now).Usable);
    }
}
