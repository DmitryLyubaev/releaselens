using Microsoft.Extensions.Primitives;
using ReleaseLens.Web;

namespace ReleaseLens.Web.Tests;

public sealed class PrincipalTests
{
    [Theory]
    [InlineData(CallerKind.Owner)]
    [InlineData(CallerKind.Deployment)]
    public void ExpectedObjectAndRoleAreRequired(CallerKind kind) => Assert.Equal(kind, EasyAuthPrincipalReader.Read(Fixtures.Headers(Fixtures.Principal(kind)), Fixtures.Options()));

    [Theory]
    [InlineData(null)]
    [InlineData("bad-base64")]
    [InlineData("e30=")]
    [InlineData("W10=")]
    public void MissingOrMalformedPrincipalIsDenied(string? value) => Assert.Equal(CallerKind.None, EasyAuthPrincipalReader.Read(Fixtures.Headers(value), Fixtures.Options()));

    [Fact]
    public void LegacyOidAndRoleAliasesAreAccepted() => Assert.Equal(CallerKind.Owner, EasyAuthPrincipalReader.Read(Fixtures.Headers(Fixtures.Claims(("http://schemas.microsoft.com/identity/claims/objectidentifier", Fixtures.Owner), ("http://schemas.microsoft.com/ws/2008/06/identity/claims/role", "WebApp.Use"))), Fixtures.Options()));

    [Theory]
    [InlineData("WebApp.use")]
    [InlineData("WebApp.Smoke")]
    [InlineData("")]
    public void OwnerRequiresUseRole(string role) => Assert.Equal(CallerKind.None, EasyAuthPrincipalReader.Read(Fixtures.Headers(Fixtures.Claims(("oid", Fixtures.Owner), ("roles", role))), Fixtures.Options()));

    [Fact]
    public void UnknownPrincipalIsDenied() => Assert.Equal(CallerKind.None, EasyAuthPrincipalReader.Read(Fixtures.Headers(Fixtures.Claims(("oid", Fixtures.Tenant), ("roles", "WebApp.Use"))), Fixtures.Options()));

    [Fact]
    public void DeploymentRequiresSmokeRole() => Assert.Equal(CallerKind.None, EasyAuthPrincipalReader.Read(Fixtures.Headers(Fixtures.Claims(("oid", Fixtures.Deploy), ("roles", "WebApp.Use"))), Fixtures.Options()));

    [Theory]
    [InlineData(true)]
    [InlineData(false)]
    public void DuplicateOrConflictingObjectClaimsAreDenied(bool duplicate)
    {
        var value = Fixtures.Claims(("oid", Fixtures.Owner), ("http://schemas.microsoft.com/identity/claims/objectidentifier", duplicate ? Fixtures.Owner : Fixtures.Deploy), ("roles", "WebApp.Use"));
        Assert.Equal(CallerKind.None, EasyAuthPrincipalReader.Read(Fixtures.Headers(value), Fixtures.Options()));
    }

    [Fact]
    public void DuplicateHeadersAreDenied()
    {
        var headers = Fixtures.Headers(null);
        headers["X-MS-CLIENT-PRINCIPAL"] = new StringValues([Fixtures.Principal(CallerKind.Owner), Fixtures.Principal(CallerKind.Owner)]);
        Assert.Equal(CallerKind.None, EasyAuthPrincipalReader.Read(headers, Fixtures.Options()));
    }

    [Fact]
    public void DuplicateSecurityRolesAreDenied() => Assert.Equal(CallerKind.None, EasyAuthPrincipalReader.Read(Fixtures.Headers(Fixtures.Claims(("oid", Fixtures.Owner), ("roles", "WebApp.Use"), ("roles", "WebApp.Use"))), Fixtures.Options()));

    [Fact]
    public void ArbitraryAliasesAreDenied() => Assert.Equal(CallerKind.None, EasyAuthPrincipalReader.Read(Fixtures.Headers(Fixtures.Claims(("objectId", Fixtures.Owner), ("role", "WebApp.Use"))), Fixtures.Options()));
}
