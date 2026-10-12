using System.Reflection;

namespace ReleaseLens.Web;

public static class BuildInfo
{
    public static string Version { get; } = typeof(BuildInfo).Assembly.GetCustomAttribute<AssemblyInformationalVersionAttribute>()?.InformationalVersion ?? "unknown";
}
