using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.AspNetCore.TestHost;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Time.Testing;
using Microsoft.Extensions.Logging;

namespace ReleaseLens.Web.Tests;

public sealed class WebAppFactory : WebApplicationFactory<Program>
{
    public StubHttpHandler Stub { get; } = new();
    public FakeTimeProvider Clock { get; } = new(new DateTimeOffset(2026, 10, 12, 0, 0, 0, TimeSpan.Zero));
    public Dictionary<string, string?> Config { get; } = Fixtures.Configuration();
    public CapturedLogs Logs { get; } = new();
    protected override void ConfigureWebHost(IWebHostBuilder builder)
    {
        builder.UseEnvironment("Production");
        builder.ConfigureAppConfiguration((_, c) => c.AddInMemoryCollection(Config));
        builder.ConfigureTestServices(services =>
        {
            services.AddSingleton<TimeProvider>(Clock);
            services.AddSingleton(new HttpClient(Stub) { Timeout = TimeSpan.FromSeconds(60) });
            services.AddLogging(logging => logging.AddProvider(Logs));
        });
    }
    public HttpClient As(ReleaseLens.Web.CallerKind kind)
    {
        var client = CreateClient(new WebApplicationFactoryClientOptions { AllowAutoRedirect = false });
        client.DefaultRequestHeaders.Add("X-MS-CLIENT-PRINCIPAL", Fixtures.Principal(kind));
        return client;
    }
}

public sealed class CapturedLogs : ILoggerProvider
{
    public System.Collections.Concurrent.ConcurrentQueue<string> Entries { get; } = new();
    public ILogger CreateLogger(string categoryName) => new Capture(Entries);
    public void Dispose() { }
    private sealed class Capture(System.Collections.Concurrent.ConcurrentQueue<string> entries) : ILogger
    {
        public IDisposable? BeginScope<TState>(TState state) where TState : notnull => null;
        public bool IsEnabled(LogLevel logLevel) => true;
        public void Log<TState>(LogLevel logLevel, EventId eventId, TState state, Exception? exception, Func<TState, Exception?, string> formatter) => entries.Enqueue(formatter(state, exception));
    }
}
