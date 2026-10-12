using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.RazorPages;

namespace ReleaseLens.Web.Pages;

public sealed class IndexModel(QuestionService questions) : PageModel
{
    [BindProperty] public string Question { get; set; } = "";
    public QuestionOutcome? Outcome { get; private set; }
    public string? Message => Outcome?.Kind switch
    {
        QuestionKind.InvalidQuestion => "Enter one question of 1 to 2,000 characters.",
        QuestionKind.SignInRequired => "Sign in again to continue. Your question is still here.",
        QuestionKind.Forbidden => "Your account does not have permission to ask this question.",
        QuestionKind.RateLimited => "The request limit has been reached. Please try again later.",
        QuestionKind.Unavailable => "The service is temporarily unavailable. Please try again later.",
        _ => null
    };
    public void OnGet() { }
    public async Task<IActionResult> OnPostAsync(CancellationToken cancellationToken)
    {
        var headers = Request.Headers["X-MS-TOKEN-AAD-ACCESS-TOKEN"];
        var caller = HttpContext.Items[typeof(CallerKind)] is CallerKind kind ? kind : CallerKind.None;
        Outcome = Request.Form["Question"].Count != 1
            ? new QuestionOutcome(QuestionKind.InvalidQuestion)
            : await questions.AnswerAsync(caller, Question ?? "", headers.Count == 1 ? headers[0] : null, cancellationToken);
        Response.StatusCode = Outcome.Kind switch
        {
            QuestionKind.Success => 200, QuestionKind.InvalidQuestion => 400, QuestionKind.SignInRequired => 401,
            QuestionKind.Forbidden => 403, QuestionKind.RateLimited => 429, _ => 503
        };
        return Page();
    }
}
