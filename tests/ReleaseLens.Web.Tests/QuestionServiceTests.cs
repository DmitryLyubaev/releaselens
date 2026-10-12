using Microsoft.Extensions.Time.Testing;
using ReleaseLens.Web;

namespace ReleaseLens.Web.Tests;

public sealed class QuestionServiceTests
{
    [Theory]
    [InlineData(CallerKind.None)]
    [InlineData(CallerKind.Deployment)]
    public async Task NonOwnerNeverCallsGateway(CallerKind caller)
    {
        var (service, stub, clock) = Create();
        var result = await service.AnswerAsync(caller, "question", Fixtures.GatewayToken(clock.GetUtcNow().AddHours(1)), TestContext.Current.CancellationToken);
        Assert.Equal(QuestionKind.Forbidden, result.Kind);
        Assert.Empty(stub.Requests);
    }

    [Theory]
    [InlineData(null)]
    [InlineData("bad")]
    public async Task MissingOrMalformedTokenNeverCallsGateway(string? token)
    {
        var (service, stub, _) = Create();
        var result = await service.AnswerAsync(CallerKind.Owner, "question", token, TestContext.Current.CancellationToken);
        Assert.Equal(QuestionKind.SignInRequired, result.Kind);
        Assert.Empty(stub.Requests);
    }

    [Theory]
    [InlineData("tid")]
    [InlineData("aud")]
    [InlineData("oid")]
    [InlineData("scp")]
    [InlineData("roles")]
    [InlineData("exp")]
    public async Task ExpiredOrWrongRequiredClaimNeverCallsGateway(string key)
    {
        var (service, stub, _) = Create();
        var claims = GatewayTokenTests.Claims(); claims[key] = key == "exp" ? 0 : "wrong";
        var result = await service.AnswerAsync(CallerKind.Owner, "question", Fixtures.Token(claims), TestContext.Current.CancellationToken);
        Assert.Equal(QuestionKind.SignInRequired, result.Kind);
        Assert.Empty(stub.Requests);
    }

    [Theory]
    [InlineData(0)]
    [InlineData(2001)]
    public async Task EmptyOrOversizeInputNeverCallsGateway(int length)
    {
        var (service, stub, clock) = Create();
        var result = await service.AnswerAsync(CallerKind.Owner, length == 0 ? " \t\n " : new string('x', length), Fixtures.GatewayToken(clock.GetUtcNow().AddHours(1)), TestContext.Current.CancellationToken);
        Assert.Equal(QuestionKind.InvalidQuestion, result.Kind);
        Assert.Empty(stub.Requests);
    }

    [Fact]
    public async Task OwnerRequestForwardsOnlyItsBearerAndPinnedLimits()
    {
        var (service, stub, clock) = Create();
        var token = Fixtures.GatewayToken(clock.GetUtcNow().AddHours(1));
        var result = await service.AnswerAsync(CallerKind.Owner, " question ", token, TestContext.Current.CancellationToken);
        Assert.Equal(QuestionKind.Success, result.Kind);
        var sent = Assert.Single(stub.Requests);
        Assert.Equal("POST", sent.Method);
        Assert.Equal("Bearer", sent.Authorization);
        Assert.True(stub.Bearers.Single() == token);
        Assert.Equal("https://gateway.example.test/azure-openai/v1/chat/completions", sent.Uri?.AbsoluteUri);
        using var body = System.Text.Json.JsonDocument.Parse(sent.Body!);
        Assert.Equal(256, body.RootElement.GetProperty("max_completion_tokens").GetInt32());
        Assert.False(body.RootElement.GetProperty("stream").GetBoolean());
        Assert.Equal("test-model", body.RootElement.GetProperty("model").GetString());
        Assert.Equal("question", body.RootElement.GetProperty("messages")[0].GetProperty("content").GetString());
    }

    private static (QuestionService Service, StubHttpHandler Stub, FakeTimeProvider Clock) Create()
    {
        var stub = new StubHttpHandler();
        var clock = new FakeTimeProvider(new DateTimeOffset(2026, 10, 12, 0, 0, 0, TimeSpan.Zero));
        var options = Fixtures.Options();
        return (new QuestionService(options, clock, new GatewayClient(new HttpClient(stub), options, clock)), stub, clock);
    }
}
