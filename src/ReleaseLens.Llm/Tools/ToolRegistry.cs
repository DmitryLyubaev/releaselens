using System.Text.Json;
using ReleaseLens.Llm.Providers;
using ReleaseLens.Storage;

namespace ReleaseLens.Llm.Tools;

public sealed class ToolRegistry(IReadOnlyList<IEvidenceTool> tools)
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
            return ToolExecutionResult.Error($"Tool '{name}' failed: {exception.Message}");
        }
    }
}
