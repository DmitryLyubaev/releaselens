using System.Net;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text.Json;

namespace ReleaseLens.Web;

public sealed class GatewayClient(HttpClient http, WebOptions options, TimeProvider clock)
{
    private const int SuccessBodyLimit = 1024 * 1024;
    public async Task<QuestionOutcome> AskAsync(string question, string bearer, CancellationToken cancellationToken)
    {
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(60), clock);
        using var linked = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken, timeout.Token);
        try
        {
            using var request = new HttpRequestMessage(HttpMethod.Post, new Uri(options.GatewayBaseUrl, "chat/completions"));
            request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", bearer);
            request.Content = JsonContent.Create(new { model = options.Model, messages = new[] { new { role = "user", content = question } }, stream = false, max_completion_tokens = 256 });
            using var response = await http.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, linked.Token);
            if (response.StatusCode != HttpStatusCode.OK)
            {
                // Do not read refusal bodies, including redirects and chunked error responses.
                return response.StatusCode switch
                {
                    HttpStatusCode.Unauthorized => new(QuestionKind.SignInRequired),
                    HttpStatusCode.Forbidden => new(QuestionKind.Forbidden),
                    HttpStatusCode.TooManyRequests => new(QuestionKind.RateLimited, RetryAfterSeconds: RetryAfter(response)),
                    _ => new(QuestionKind.Unavailable)
                };
            }
            if (response.Content.Headers.ContentLength > SuccessBodyLimit) return new(QuestionKind.Unavailable);
            using var stream = await response.Content.ReadAsStreamAsync(linked.Token);
            using var buffer = new MemoryStream();
            var chunk = new byte[8192];
            while (true)
            {
                var read = await stream.ReadAsync(chunk.AsMemory(0, (int)Math.Min(chunk.Length, SuccessBodyLimit + 1L - buffer.Length)), linked.Token);
                if (read == 0) break;
                await buffer.WriteAsync(chunk.AsMemory(0, read), linked.Token);
                if (buffer.Length > SuccessBodyLimit) return new(QuestionKind.Unavailable);
            }
            using var document = JsonDocument.Parse(buffer.GetBuffer().AsMemory(0, (int)buffer.Length));
            var answer = document.RootElement.GetProperty("choices")[0].GetProperty("message").GetProperty("content");
            if (answer.ValueKind != JsonValueKind.String || string.IsNullOrWhiteSpace(answer.GetString())) return new(QuestionKind.Unavailable);
            return new(QuestionKind.Success, answer.GetString());
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested) { throw; }
        catch (Exception ex) when (ex is HttpRequestException or OperationCanceledException or JsonException or KeyNotFoundException or InvalidOperationException or IndexOutOfRangeException or IOException or FormatException)
        { return new(QuestionKind.Unavailable); }
    }

    private int? RetryAfter(HttpResponseMessage response)
    {
        var value = response.Headers.RetryAfter;
        double? seconds = value?.Delta?.TotalSeconds;
        if (seconds is null && value?.Date is { } date) seconds = Math.Ceiling((date - clock.GetUtcNow()).TotalSeconds);
        return seconds is >= 0 and <= 3600 ? (int)seconds.Value : null;
    }
}
