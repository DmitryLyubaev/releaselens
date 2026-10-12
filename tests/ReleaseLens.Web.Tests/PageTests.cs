using System.Net;
using System.Text.Json;
using System.Text.RegularExpressions;
using ReleaseLens.Web;

namespace ReleaseLens.Web.Tests;

public sealed class PageTests
{
    [Fact]
    public async Task ValidOwnerFormCallsOnceAndEncodesModelText()
    {
        await using var factory = new WebAppFactory();
        factory.Stub.Reply = (_, _) => Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK)
        {
            Content = new StringContent(JsonSerializer.Serialize(new { choices = new[] { new { message = new { content = "<script>model</script>\nanswer" } } } }))
        });
        using var client = Owner(factory);
        using var reply = await Post(client, "Explain staging");
        Assert.Equal(HttpStatusCode.OK, reply.StatusCode);
        var text = await reply.Content.ReadAsStringAsync(TestContext.Current.CancellationToken);
        Assert.Contains("id=\"answer-text\"", text);
        Assert.Contains("&lt;script&gt;model&lt;/script&gt;", text);
        Assert.DoesNotContain("<script>model", text);
        Assert.Single(factory.Stub.Requests);
        Assert.True(reply.Headers.CacheControl?.NoStore);
        using var fresh = await client.GetAsync("/", TestContext.Current.CancellationToken);
        Assert.DoesNotContain("id=\"answer-text\"", await fresh.Content.ReadAsStringAsync(TestContext.Current.CancellationToken));
    }

    [Theory]
    [InlineData(401, "Sign in again")]
    [InlineData(403, "permission")]
    [InlineData(429, "limit")]
    [InlineData(503, "unavailable")]
    public async Task GatewayRefusalHasSafeMessageAndRetainsQuestion(int status, string expected)
    {
        await using var factory = new WebAppFactory();
        factory.Stub.Reply = (_, _) => Task.FromResult(new HttpResponseMessage((HttpStatusCode)status) { Content = new UnreadableContent() });
        using var reply = await Post(Owner(factory), "Question retained");
        Assert.Equal(status, (int)reply.StatusCode);
        var text = await reply.Content.ReadAsStringAsync(TestContext.Current.CancellationToken);
        Assert.Contains(expected, text, StringComparison.OrdinalIgnoreCase);
        Assert.Contains("Question retained", text);
        Assert.DoesNotContain("gateway.example.test", text);
        Assert.Single(factory.Stub.Requests);
    }

    [Theory]
    [InlineData(false)]
    [InlineData(true)]
    public async Task MissingOrExpiredGatewayTokenHasNoOutboundCall(bool expired)
    {
        await using var factory = new WebAppFactory();
        using var client = factory.As(CallerKind.Owner);
        if (expired) client.DefaultRequestHeaders.Add("X-MS-TOKEN-AAD-ACCESS-TOKEN", Fixtures.GatewayToken(factory.Clock.GetUtcNow().AddMinutes(-1)));
        using var reply = await Post(client, "Question retained");
        Assert.Equal(HttpStatusCode.Unauthorized, reply.StatusCode);
        Assert.Contains("Sign in again", await reply.Content.ReadAsStringAsync(TestContext.Current.CancellationToken));
        Assert.Empty(factory.Stub.Requests);
    }

    [Theory]
    [InlineData(0)]
    [InlineData(2001)]
    public async Task InvalidInputHasNoOutboundCall(int length)
    {
        await using var factory = new WebAppFactory();
        using var reply = await Post(Owner(factory), length == 0 ? " \t " : new string('x', length));
        Assert.Equal(HttpStatusCode.BadRequest, reply.StatusCode);
        Assert.Empty(factory.Stub.Requests);
    }

    [Theory]
    [InlineData(null)]
    [InlineData("forged")]
    public async Task MissingOrForgedAntiforgeryHasNoOutboundCall(string? csrf)
    {
        await using var factory = new WebAppFactory();
        using var client = Owner(factory);
        await client.GetAsync("/", TestContext.Current.CancellationToken);
        var form = new Dictionary<string, string> { ["Question"] = "question" };
        if (csrf is not null) form["__RequestVerificationToken"] = csrf;
        using var reply = await client.PostAsync("/ask", new FormUrlEncodedContent(form), TestContext.Current.CancellationToken);
        Assert.Equal(HttpStatusCode.BadRequest, reply.StatusCode);
        Assert.Empty(factory.Stub.Requests);
    }

    [Fact]
    public async Task DeploymentPostHasNoOutboundCall()
    {
        await using var factory = new WebAppFactory();
        using var reply = await factory.As(CallerKind.Deployment).PostAsync("/ask", new FormUrlEncodedContent(new Dictionary<string, string> { ["Question"] = "question" }), TestContext.Current.CancellationToken);
        Assert.Equal(HttpStatusCode.Forbidden, reply.StatusCode);
        Assert.Empty(factory.Stub.Requests);
    }

    [Theory]
    [InlineData(true)]
    [InlineData(false)]
    public async Task FormBodyOver16KiBIsRejectedBeforeGateway(bool knownLength)
    {
        await using var factory = new WebAppFactory();
        using var client = Owner(factory);
        var csrf = await Csrf(client);
        var payload = $"Question=x&__RequestVerificationToken={Uri.EscapeDataString(csrf)}&padding={new string('x', 16385)}";
        HttpContent content = knownLength ? new StringContent(payload) : new StreamContent(new UnknownLengthStream(payload));
        content.Headers.ContentType = new System.Net.Http.Headers.MediaTypeHeaderValue("application/x-www-form-urlencoded");
        using var reply = await client.PostAsync("/ask", content, TestContext.Current.CancellationToken);
        Assert.Equal(HttpStatusCode.RequestEntityTooLarge, reply.StatusCode);
        Assert.Empty(factory.Stub.Requests);
    }

    [Fact]
    public async Task OwnerPageHasNoPersonalIdentityAndSignedOutHasOnlyFixedSignIn()
    {
        await using var factory = new WebAppFactory();
        using var client = Owner(factory);
        using var response = await client.GetAsync("/", TestContext.Current.CancellationToken);
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        var text = await response.Content.ReadAsStringAsync(TestContext.Current.CancellationToken);
        Assert.Contains("Signed in", text);
        Assert.DoesNotContain(Fixtures.Owner, text);
        Assert.DoesNotContain(Fixtures.GatewayToken(factory.Clock.GetUtcNow().AddMinutes(10)), text);
        Assert.Contains("action=\"/ask\"", text);
        Assert.Contains("/.auth/logout?post_logout_redirect_uri=/signed-out", text);
        var preview = Environment.GetEnvironmentVariable("RL_OFFLINE_PREVIEW");
        if (preview is not null) await File.WriteAllTextAsync(preview, text, TestContext.Current.CancellationToken);
        using var signedOut = await factory.CreateClient().GetAsync("/signed-out?returnUrl=https://example.invalid/", TestContext.Current.CancellationToken);
        var signedOutText = await signedOut.Content.ReadAsStringAsync(TestContext.Current.CancellationToken);
        Assert.DoesNotContain("<form", signedOutText);
        Assert.DoesNotContain("//example.invalid", signedOutText);
        Assert.Contains("/.auth/login/aad?post_login_redirect_uri=/", signedOutText);
    }

    private static HttpClient Owner(WebAppFactory factory)
    {
        var client = factory.As(CallerKind.Owner);
        client.DefaultRequestHeaders.Add("X-MS-TOKEN-AAD-ACCESS-TOKEN", Fixtures.GatewayToken(factory.Clock.GetUtcNow().AddMinutes(10)));
        return client;
    }
    private static async Task<string> Csrf(HttpClient client)
    {
        using var response = await client.GetAsync("/", TestContext.Current.CancellationToken);
        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        var text = await response.Content.ReadAsStringAsync(TestContext.Current.CancellationToken);
        var match = Regex.Match(text, "name=\"__RequestVerificationToken\"[^>]*value=\"([^\"]+)\"");
        Assert.True(match.Success, "Owner form must contain its antiforgery token");
        return WebUtility.HtmlDecode(match.Groups[1].Value);
    }
    private static async Task<HttpResponseMessage> Post(HttpClient client, string question)
    {
        var csrf = await Csrf(client);
        return await client.PostAsync("/ask", new FormUrlEncodedContent(new Dictionary<string, string> { ["Question"] = question, ["__RequestVerificationToken"] = csrf }), TestContext.Current.CancellationToken);
    }
}
