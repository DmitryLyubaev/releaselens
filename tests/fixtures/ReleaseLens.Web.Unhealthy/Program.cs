// Dedicated negative package. It cannot call the gateway and is never promotable.
var builder = WebApplication.CreateBuilder(args);
builder.Logging.ClearProviders();
var app = builder.Build();
var version = typeof(Program).Assembly.GetCustomAttributes(typeof(System.Reflection.AssemblyInformationalVersionAttribute), false)
    .Cast<System.Reflection.AssemblyInformationalVersionAttribute>().Single().InformationalVersion;
app.MapGet("/health", (HttpContext context) =>
{
    context.Response.Headers["X-ReleaseLens-Unhealthy"] = "1";
    context.Response.Headers["X-ReleaseLens-Fixture-Version"] = version;
    return Results.StatusCode(503);
});
app.MapFallback(() => Results.StatusCode(503));
app.Run();
