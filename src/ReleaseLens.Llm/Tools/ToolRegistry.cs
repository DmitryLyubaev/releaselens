using System.Text.Json;
using Microsoft.Extensions.Logging;
using ReleaseLens.Llm.Providers;
using ReleaseLens.Storage;

namespace ReleaseLens.Llm.Tools;

public sealed class ToolRegistry(IReadOnlyList<IEvidenceTool> tools, ILogger<ToolRegistry> logger)
{
    private readonly Dictionary<string, IEvidenceTool> _byName =
        tools.ToDictionary(t => t.Name, StringComparer.Ordinal);

    public IReadOnlyList<ToolDefinition> Definitions { get; } =
        [.. tools.Select(t => new ToolDefinition(t.Name, t.Description, t.JsonSchema))];

    public async Task<ToolExecutionResult> ExecuteAsync(
        string name, TenantScope scope, JsonElement arguments, CancellationToken cancellationToken)
    {
        if (!_byName.TryGetValue(name, out var tool))
        {
            return ToolExecutionResult.Error(
                $"No tool named '{name}'. Available tools: {string.Join(", ", _byName.Keys)}.");
        }

        try
        {
            return await tool.ExecuteAsync(scope, arguments, cancellationToken);
        }
        catch (Exception exception) when (exception is not OperationCanceledException)
        {
            // The exception's own message is not returned: /query used to hand it straight
            // to the model, but POST /evidence/tools/{name} now hands it to an external
            // caller over HTTP, and a driver exception (a PostgresException's SQLSTATE and
            // server text, for one) is not something this API is willing to leak. Logged
            // here because this is the only place that still has it.
            logger.LogError(exception, "Tool '{ToolName}' failed", name);
            return ToolExecutionResult.Error($"Tool '{name}' failed. See server logs for details.");
        }
    }
}
