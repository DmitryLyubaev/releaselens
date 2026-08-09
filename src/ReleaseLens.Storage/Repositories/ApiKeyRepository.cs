using System.Security.Cryptography;
using System.Text;
using Dapper;

namespace ReleaseLens.Storage.Repositories;

/// <summary>
/// Keys are stored as SHA-256 hashes. The plaintext exists only in the response to
/// CreateKeyAsync — a stolen database yields nothing an attacker can present.
/// </summary>
public sealed class ApiKeyRepository(TenantConnectionFactory factory)
{
    private const string Prefix = "rl_";

    public async Task<string> CreateKeyAsync(Guid tenantId, string label, CancellationToken cancellationToken)
    {
        var key = Prefix + Convert.ToHexStringLower(RandomNumberGenerator.GetBytes(24));

        await using var connection = await factory.OpenUntenantedAsync(cancellationToken);
        await connection.ExecuteAsync(
            """
            insert into api_keys (api_key_id, tenant_id, key_hash, label)
            values (gen_random_uuid(), @tenantId, @hash, @label)
            """,
            new { tenantId, hash = Hash(key), label });

        return key;
    }

    public async Task<Guid?> ResolveTenantAsync(string presentedKey, CancellationToken cancellationToken)
    {
        if (string.IsNullOrWhiteSpace(presentedKey))
        {
            return null;
        }

        await using var connection = await factory.OpenUntenantedAsync(cancellationToken);

        return await connection.ExecuteScalarAsync<Guid?>(
            "select tenant_id from api_keys where key_hash = @hash and revoked_at is null",
            new { hash = Hash(presentedKey) });
    }

    private static byte[] Hash(string key) => SHA256.HashData(Encoding.UTF8.GetBytes(key));
}
