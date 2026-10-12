namespace ReleaseLens.Web;

public sealed class QuestionService(WebOptions options, TimeProvider clock, GatewayClient gateway)
{
    public Task<QuestionOutcome> AnswerAsync(CallerKind caller, string question, string? gatewayToken, CancellationToken cancellationToken)
    {
        if (caller != CallerKind.Owner) return Task.FromResult(new QuestionOutcome(QuestionKind.Forbidden));
        var trimmed = question.Trim();
        if (trimmed.Length is < 1 or > 2000) return Task.FromResult(new QuestionOutcome(QuestionKind.InvalidQuestion));
        if (!GatewayTokenInspector.Inspect(gatewayToken, options, clock.GetUtcNow()).Usable)
            return Task.FromResult(new QuestionOutcome(QuestionKind.SignInRequired));
        return gateway.AskAsync(trimmed, gatewayToken!, cancellationToken);
    }
}
