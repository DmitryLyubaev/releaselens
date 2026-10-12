using ReleaseLens.Web;

var builder = WebApplication.CreateBuilder(args);
// Request URLs, platform claims and option values must not enter logs.
builder.Logging.ClearProviders();
builder.Services.AddSingleton(sp => WebOptions.From(sp.GetRequiredService<IConfiguration>()));
builder.Services.AddSingleton(TimeProvider.System);
var app = builder.Build();
app.Use(async (context, next) =>
{
    context.Response.Headers.CacheControl = "no-store";
    context.Response.Headers["X-Content-Type-Options"] = "nosniff";
    if (context.Request.Path == "/signed-out") { await next(context); return; }
    var options = context.RequestServices.GetRequiredService<WebOptions>();
    if (!options.IsValid)
    {
        context.Response.StatusCode = StatusCodes.Status503ServiceUnavailable;
        await context.Response.WriteAsJsonAsync(new { status = "unavailable", version = BuildInfo.Version, slot = "unknown", configurationValid = false, caller = "none" });
        return;
    }
    var caller = EasyAuthPrincipalReader.Read(context.Request.Headers, options);
    context.Items[typeof(CallerKind)] = caller;
    if (caller == CallerKind.None)
    {
        if (!context.Request.Headers.ContainsKey("X-MS-CLIENT-PRINCIPAL")) context.Response.Redirect("/.auth/login/aad?post_login_redirect_uri=/");
        else context.Response.StatusCode = StatusCodes.Status403Forbidden;
        return;
    }
    if (caller == CallerKind.Deployment && context.Request.Path != "/health")
    {
        context.Response.StatusCode = StatusCodes.Status403Forbidden;
        return;
    }
    try { await next(context); }
    catch (Exception)
    {
        if (!context.Response.HasStarted)
        {
            context.Response.Clear();
            context.Response.Headers.CacheControl = "no-store";
            context.Response.StatusCode = StatusCodes.Status503ServiceUnavailable;
            await context.Response.WriteAsync("Service temporarily unavailable.");
        }
    }
});
app.MapGet("/health", (HttpContext context, WebOptions options) => Results.Json(new
{
    status = "ok", version = BuildInfo.Version, slot = options.Slot, configurationValid = true,
    caller = context.Items[typeof(CallerKind)] is CallerKind.Owner ? "owner" : "deployment"
}));
app.MapGet("/session", () => Results.StatusCode(503));
app.MapGet("/", () => Results.StatusCode(503));
app.MapPost("/ask", () => Results.StatusCode(503));
app.MapGet("/signed-out", () => Results.Content("<p>Signed out.</p><a href=\"/.auth/login/aad?post_login_redirect_uri=/\">Sign in</a>", "text/html"));
app.Run();
public partial class Program;
