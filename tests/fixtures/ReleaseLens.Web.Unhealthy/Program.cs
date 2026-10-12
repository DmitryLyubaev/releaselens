// Dedicated negative package. It cannot call the gateway and is never promotable.
var builder = WebApplication.CreateBuilder(args);
builder.Logging.ClearProviders();
var app = builder.Build();
app.MapGet("/health", () => Results.StatusCode(503));
app.MapFallback(() => Results.StatusCode(503));
app.Run();
