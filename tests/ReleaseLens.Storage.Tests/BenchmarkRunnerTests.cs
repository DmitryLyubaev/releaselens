using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using ReleaseLens.Storage.Retrieval;
using Xunit;

namespace ReleaseLens.Storage.Tests;

public class BenchmarkRunnerTests
{
    // Extra fields such as the frozen file's target and entity_type are carried on purpose.
    private const string Questions = """
        {"qid": "q1", "question": "Which change added retries?", "target": "commit:aaaa111", "entity_type": "commit"}
        {"qid": "q2", "question": "Which release dropped Python 3.9?", "target": "release:v1", "entity_type": "release"}
        {"qid": "q3", "question": "Which issue reported the timeout?", "target": "issue:7", "entity_type": "issue"}
        """;

    private static IReadOnlyList<(long ChunkId, string Artefact, double Score)> SixtyHits() =>
        [.. Enumerable.Range(0, 60).Select(i => ((long)i, $"commit:sha{i}", 1.0 - (i / 100.0)))];

    private static async Task<List<JsonElement>> RunAsync(
        string mode,
        Func<string, CancellationToken, Task<IReadOnlyList<(long ChunkId, string Artefact, double Score)>>> search)
    {
        var output = new StringWriter();
        await BenchmarkRunner.RunRetrieveAsync(
            mode, new StringReader(Questions), output, search, TestContext.Current.CancellationToken);

        return [.. output.ToString()
            .Split('\n', StringSplitOptions.RemoveEmptyEntries)
            .Select(line => JsonDocument.Parse(line).RootElement)];
    }

    [Fact]
    public async Task Retrieve_WritesOneLinePerQuestionWithFiftyHits()
    {
        var lines = await RunAsync("hybrid", (_, _) => Task.FromResult(SixtyHits()));

        Assert.Equal(["q1", "q2", "q3"], lines.Select(l => l.GetProperty("qid").GetString()));

        foreach (var line in lines)
        {
            Assert.Equal("S1", line.GetProperty("arm").GetString());
            Assert.Equal(JsonValueKind.Null, line.GetProperty("error").ValueKind);
            Assert.True(line.GetProperty("ms").GetDouble() >= 0);

            var hits = line.GetProperty("hits").EnumerateArray().ToList();
            Assert.Equal(50, hits.Count);

            // The first 50, in the order the search returned them.
            Assert.Equal(0, hits[0].GetProperty("chunkId").GetInt64());
            Assert.Equal("commit:sha0", hits[0].GetProperty("artefact").GetString());
            Assert.Equal(1.0, hits[0].GetProperty("score").GetDouble());
            Assert.Equal(49, hits[49].GetProperty("chunkId").GetInt64());
        }
    }

    [Fact]
    public async Task Retrieve_RecordsAnErrorAndContinues()
    {
        var lines = await RunAsync("bge-exact", (question, _) =>
            question.Contains("Python", StringComparison.Ordinal)
                ? throw new InvalidOperationException("search failed for q2")
                : Task.FromResult(SixtyHits()));

        Assert.Equal(3, lines.Count);
        Assert.All(lines, line => Assert.Equal("E1", line.GetProperty("arm").GetString()));

        Assert.Equal("q2", lines[1].GetProperty("qid").GetString());
        Assert.Contains("search failed for q2", lines[1].GetProperty("error").GetString());
        Assert.Empty(lines[1].GetProperty("hits").EnumerateArray());

        Assert.Equal("q3", lines[2].GetProperty("qid").GetString());
        Assert.Equal(JsonValueKind.Null, lines[2].GetProperty("error").ValueKind);
        Assert.Equal(50, lines[2].GetProperty("hits").GetArrayLength());
    }

    /// <summary>
    /// JSON has no form for NaN, so a hit scored NaN (say, from a zero query vector) cannot be
    /// written. That is the question's error, and the run moves on to the next one.
    /// </summary>
    [Fact]
    public async Task Retrieve_AHitThatCannotBeWrittenIsThatQuestionsError()
    {
        var lines = await RunAsync("bge-exact", (question, _) =>
            Task.FromResult<IReadOnlyList<(long ChunkId, string Artefact, double Score)>>(
                question.Contains("Python", StringComparison.Ordinal)
                    ? [(1L, "commit:sha1", double.NaN)]
                    : SixtyHits()));

        Assert.Equal(["q1", "q2", "q3"], lines.Select(l => l.GetProperty("qid").GetString()));
        Assert.False(string.IsNullOrEmpty(lines[1].GetProperty("error").GetString()));
        Assert.Empty(lines[1].GetProperty("hits").EnumerateArray());
        Assert.Equal(JsonValueKind.Null, lines[2].GetProperty("error").ValueKind);
        Assert.Equal(50, lines[2].GetProperty("hits").GetArrayLength());
    }

    [Fact]
    public async Task Retrieve_RejectsAnUnknownMode()
    {
        var called = false;

        var error = await Assert.ThrowsAsync<ArgumentException>(() => RunAsync("approximate", (_, _) =>
        {
            called = true;
            return Task.FromResult(SixtyHits());
        }));

        Assert.Contains("hybrid", error.Message);
        Assert.Contains("bge-exact", error.Message);
        Assert.False(called);
    }

    /// <summary>
    /// The binding note from Task 1: a SQL error aborts the scope's transaction, so a shared
    /// scope would fail every question after the first bad one. The fake scope models that:
    /// once a search throws inside it, every later search in the same scope throws too.
    /// </summary>
    [Fact]
    public async Task Retrieve_AFailedQuestionDoesNotPoisonTheNextOnesScope()
    {
        var opened = new List<FakeScope>();

        var search = BenchmarkRunner.PerQuestionScope<FakeScope>(
            _ =>
            {
                var scope = new FakeScope();
                opened.Add(scope);
                return Task.FromResult(scope);
            },
            (scope, question, _) =>
            {
                if (scope.Aborted)
                {
                    throw new InvalidOperationException("current transaction is aborted");
                }

                if (question.Contains("Python", StringComparison.Ordinal))
                {
                    scope.Aborted = true;
                    throw new InvalidOperationException("syntax error in q2");
                }

                return Task.FromResult(SixtyHits());
            });

        var lines = await RunAsync("bge-exact", search);

        Assert.Contains("syntax error in q2", lines[1].GetProperty("error").GetString());
        Assert.Equal(JsonValueKind.Null, lines[2].GetProperty("error").ValueKind);
        Assert.Equal(50, lines[2].GetProperty("hits").GetArrayLength());

        Assert.Equal(3, opened.Count);
        Assert.All(opened, scope => Assert.True(scope.Disposed));
    }

    [Fact]
    public async Task Retrieve_StopsOnCancellationRatherThanRecordingAnError()
    {
        using var cancellation = new CancellationTokenSource();
        var output = new StringWriter();

        await Assert.ThrowsAnyAsync<OperationCanceledException>(() => BenchmarkRunner.RunRetrieveAsync(
            "hybrid", new StringReader(Questions), output, (_, token) =>
            {
                cancellation.Cancel();
                token.ThrowIfCancellationRequested();
                return Task.FromResult(SixtyHits());
            }, cancellation.Token));

        Assert.Equal(string.Empty, output.ToString());
    }

    private sealed class FakeScope : IAsyncDisposable
    {
        public bool Aborted { get; set; }
        public bool Disposed { get; private set; }

        public ValueTask DisposeAsync()
        {
            Disposed = true;
            return ValueTask.CompletedTask;
        }
    }
}
