using System;
using System.Linq;
using ReleaseLens.Llm.Agent;
using Xunit;

namespace ReleaseLens.Llm.Tests;

/// <summary>
/// The prompt describes what the tools do. When one drifts from the other, nothing fails:
/// the code is still correct, the tests still pass, and the only symptom is a model acting
/// on a description that is no longer true.
/// </summary>
public class SystemPromptTests
{
    /// <summary>
    /// The prompt once grouped list_releases with count_evidence and told the model that both
    /// "carry no evidence marker and need none". ListReleasesTool returns real citations —
    /// releases are artefacts, not figures — so the model was being told to leave uncited the
    /// very evidence it had been handed. Sixteen of the forty-three golden queries expect a
    /// release citation, and no existing test could see the contradiction, because each half
    /// was correct on its own.
    /// </summary>
    [Fact]
    public void TheNoMarkerRule_CoversComputedFiguresOnly_NeverListReleases()
    {
        var prompt = SystemPrompt.Build("microsoft/semantic-kernel").Replace("\r\n", "\n");

        var noMarkerParagraphs = prompt
            .Split("\n\n", StringSplitOptions.RemoveEmptyEntries)
            .Where(p => p.Contains("no evidence marker", StringComparison.Ordinal))
            .ToArray();

        Assert.NotEmpty(noMarkerParagraphs);

        Assert.All(noMarkerParagraphs, paragraph =>
        {
            // The rule must still exist and still name the tool it genuinely applies to.
            Assert.Contains("count_evidence", paragraph, StringComparison.Ordinal);

            // And must never extend to a tool that returns citable artefacts.
            Assert.DoesNotContain("list_releases", paragraph, StringComparison.Ordinal);
        });
    }

    /// <summary>
    /// Every tool the registry exposes should be described to the model. A tool the prompt
    /// never mentions is one the model has no reason to reach for.
    /// </summary>
    [Fact]
    public void EveryToolTheAgentIsGiven_IsDescribedInThePrompt()
    {
        var prompt = SystemPrompt.Build("microsoft/semantic-kernel");

        string[] tools =
        [
            "search_commits", "get_issue", "diff_between_releases",
            "find_regressions", "count_evidence", "list_releases"
        ];

        Assert.All(tools, tool => Assert.Contains(tool, prompt, StringComparison.Ordinal));
    }
}
