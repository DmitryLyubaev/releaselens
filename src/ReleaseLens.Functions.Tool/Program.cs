using Azure.Core;
using Azure.Identity;
using Microsoft.Azure.Functions.Worker.Builder;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using ReleaseLens.Functions.Common;
using ReleaseLens.Functions.Tool;

var builder = FunctionsApplication.CreateBuilder(args);

// A missing setting stops the app here, by name.
var settings = ToolSettings.From(builder.Configuration);

// Everything the app calls uses the user-assigned identity; no key is read or held. The clients are
// built on first use, not at startup: the host gives the worker 30 seconds to start.
builder.Services
    .AddSingleton<TokenCredential>(
        new ManagedIdentityCredential(ManagedIdentityId.FromUserAssignedClientId(settings.ClientId)))
    .AddSingleton(services => new EmbeddingsClient(
        Authorised(services, BearerTokenHandler.OpenAiScope, settings.OpenAiBaseUrl), settings.EmbeddingDeployment))
    .AddSingleton(services => new SearchIndexClient(
        Authorised(services, BearerTokenHandler.SearchScope, settings.SearchEndpoint), settings.SearchIndex))
    .AddSingleton<CorpusSearch>();

builder.Build().Run();

// One client, and so one token cache, for the life of the process.
static HttpClient Authorised(IServiceProvider services, string scope, Uri baseAddress) =>
    new(new BearerTokenHandler(services.GetRequiredService<TokenCredential>(), scope, TimeProvider.System)
    {
        InnerHandler = new SocketsHttpHandler { PooledConnectionLifetime = TimeSpan.FromMinutes(5) },
    })
    {
        BaseAddress = baseAddress,
    };
