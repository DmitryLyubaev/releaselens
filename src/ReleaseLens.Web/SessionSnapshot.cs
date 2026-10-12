namespace ReleaseLens.Web;

public sealed record SessionSnapshot(bool TokenPresent, bool TokenParseable, bool TenantMatches, bool AudienceMatches,
    bool OwnerMatches, bool ScopeMatches, bool RoleMatches, bool Usable, long? ExpiresAtUnixSeconds);
