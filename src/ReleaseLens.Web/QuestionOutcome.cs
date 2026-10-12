namespace ReleaseLens.Web;

public enum QuestionKind { Success, InvalidQuestion, SignInRequired, Forbidden, RateLimited, Unavailable }
public sealed record QuestionOutcome(QuestionKind Kind, string? Answer = null, int? RetryAfterSeconds = null);
