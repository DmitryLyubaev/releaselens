using ReleaseLens.Web;

var builder = WebApplication.CreateBuilder(args);
// Request URLs, platform claims and option values must not enter logs.
builder.Logging.ClearProviders();
builder.Logging.AddFilter((_, _) => false);
builder.Services.AddSingleton(sp => WebOptions.From(sp.GetRequiredService<IConfiguration>()));
builder.Services.AddSingleton(TimeProvider.System);
builder.Services.AddSingleton(_ => new HttpClient(new HttpClientHandler { AllowAutoRedirect = false, UseCookies = false }) { Timeout = Timeout.InfiniteTimeSpan });
builder.Services.AddSingleton<GatewayClient>();
builder.Services.AddSingleton<QuestionService>();
builder.Services.AddRazorPages(options => options.Conventions.AddPageRoute("/Index", "/ask"));
var app = builder.Build();
app.Use(async (context, next) =>
{
    context.Response.Headers.CacheControl = "no-store";
    context.Response.Headers["X-Content-Type-Options"] = "nosniff";
    if (context.Request.Path is var publicPath && (publicPath == "/signed-out" || publicPath == "/site.css" || publicPath == "/site.js"))
    { await next(context); return; }
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
    if (context.Request.Method == "POST" && context.Request.Path == "/ask")
    {
        if (context.Request.ContentLength > 16384)
        { context.Response.StatusCode = StatusCodes.Status413PayloadTooLarge; return; }
        context.Request.EnableBuffering(bufferThreshold: 16384, bufferLimit: 16384);
        try
        {
            await context.Request.Body.CopyToAsync(Stream.Null, context.RequestAborted);
            context.Request.Body.Position = 0;
        }
        catch (IOException)
        { context.Response.StatusCode = StatusCodes.Status413PayloadTooLarge; return; }
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
app.UseStaticFiles();
app.MapGet("/health", (HttpContext context, WebOptions options) => Results.Json(new
{
    status = "ok", version = BuildInfo.Version, slot = options.Slot, configurationValid = true,
    caller = context.Items[typeof(CallerKind)] is CallerKind.Owner ? "owner" : "deployment"
}));
app.MapGet("/session", (HttpContext context, WebOptions options, TimeProvider clock) =>
{
    var headers = context.Request.Headers["X-MS-TOKEN-AAD-ACCESS-TOKEN"];
    return Results.Json(GatewayTokenInspector.Inspect(headers.Count == 1 ? headers[0] : null, options, clock.GetUtcNow()));
});
app.MapRazorPages();
app.Run();
public partial class Program;
