using System;
using System.Net.Http;
using System.Threading.Tasks;
using Azure.Identity;
using Microsoft.Extensions.Time.Testing;
using ReleaseLens.Ingestion.Tests;
using ReleaseLens.Llm.Providers;
using ReleaseLens.Llm.Providers.Azure;
using Xunit;

namespace ReleaseLens.Llm.Tests;

public class EntraTokenHandlerTests
{
    private const string Scope = "https://ai.azure.com/.default";
    private static readonly DateTimeOffset Start = new(2026, 9, 24, 9, 0, 0, TimeSpan.Zero);

    private static HttpClient Client(FakeTokenCredential credential, TimeProvider time, StubHttpMessageHandler stub) =>
        new(new EntraTokenHandler(new EntraTokenCache(credential, Scope, time)) { InnerHandler = stub })
        {
            BaseAddress = new Uri("https://example-subdomain.openai.azure.com/openai/v1/")
        };

    private static Task<HttpResponseMessage> Post(HttpClient client) =>
        client.PostAsync("chat/completions", new StringContent("{}"), TestContext.Current.CancellationToken);

    [Fact]
    public async Task Send_AttachesTheCredentialsToken_ForTheConfiguredScope()
    {
        var credential = new FakeTokenCredential().Returns("token-1", Start.AddHours(1));
        var stub = new StubHttpMessageHandler().EnqueueJson("{}");

        await Post(Client(credential, new FakeTimeProvider(Start), stub));

        var authorization = stub.Requests[0].Headers.Authorization!;
        Assert.Equal("Bearer", authorization.Scheme);
        Assert.Equal("token-1", authorization.Parameter);
        Assert.Equal([Scope], Assert.Single(credential.RequestedScopes));
    }

    [Fact]
    public async Task Send_ReusesTheTokenAcrossRequests_AndRefreshesItBeforeExpiry()
    {
        var time = new FakeTimeProvider(Start);
        var credential = new FakeTokenCredential()
            .Returns("token-1", Start.AddHours(1))
            .Returns("token-2", Start.AddHours(2));
        var stub = new StubHttpMessageHandler().EnqueueJson("{}").EnqueueJson("{}").EnqueueJson("{}");
        var client = Client(credential, time, stub);

        await Post(client);
        await Post(client);
        time.Advance(TimeSpan.FromMinutes(55));
        await Post(client);

        Assert.Equal(
            ["token-1", "token-1", "token-2"],
            stub.Requests.ConvertAll(request => request.Headers.Authorization!.Parameter));
        Assert.Equal(2, credential.Calls);
    }

    [Fact]
    public async Task Send_CredentialUnavailable_ThrowsHttpRequestException_AndSendsNothing()
    {
        var credential = new FakeTokenCredential()
            .Throws(new CredentialUnavailableException("az is not logged in."));
        var stub = new StubHttpMessageHandler();

        var failure = await Assert.ThrowsAsync<HttpRequestException>(
            () => Post(Client(credential, new FakeTimeProvider(Start), stub)));

        Assert.IsType<CredentialUnavailableException>(failure.InnerException);
        Assert.Empty(stub.Requests);
    }

    [Fact]
    public async Task Send_AuthenticationFailed_ThrowsHttpRequestException()
    {
        var credential = new FakeTokenCredential()
            .Throws(new AuthenticationFailedException("The managed identity endpoint returned 400."));

        var failure = await Assert.ThrowsAsync<HttpRequestException>(
            () => Post(Client(credential, new FakeTimeProvider(Start), new StubHttpMessageHandler())));

        Assert.IsType<AuthenticationFailedException>(failure.InnerException);
    }

    [Fact]
    public async Task Send_CredentialUnavailable_ThroughProviderHttp_IsProviderUnavailable()
    {
        var credential = new FakeTokenCredential()
            .Throws(new CredentialUnavailableException("az is not logged in."));
        var client = Client(credential, new FakeTimeProvider(Start), new StubHttpMessageHandler());

        var failure = await Assert.ThrowsAsync<ProviderUnavailableException>(() => ProviderHttp.SendAsync(
            () => Post(client), "azure-openai", TestContext.Current.CancellationToken));

        Assert.Equal("azure-openai", failure.ProviderName);
        Assert.IsType<HttpRequestException>(failure.InnerException);
    }
}
