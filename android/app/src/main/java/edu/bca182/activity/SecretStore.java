package edu.bca182.activity;

import android.content.Context;
import android.content.SharedPreferences;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;
import java.nio.charset.StandardCharsets;
import java.security.KeyStore;
import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

/** API token is encrypted with a per-install Android Keystore key. No plaintext fallback. */
final class SecretStore {
    static final class Config {
        final String base, device, token;
        Config(String base, String device, String token) {
            this.base = ContractState.baseUrl(base); ContractState.credentials(device, token);
            this.device = device; this.token = token;
        }
    }
    private final SharedPreferences prefs;
    private static final String ALIAS = "bca182-api-v1";
    SecretStore(Context context) { prefs = context.getSharedPreferences("private_api", Context.MODE_PRIVATE); }
    private SecretKey key() throws Exception {
        KeyStore store = KeyStore.getInstance("AndroidKeyStore"); store.load(null);
        if (!store.containsAlias(ALIAS)) {
            KeyGenerator generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore");
            generator.init(new KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256).build());
            generator.generateKey();
        }
        return (SecretKey)store.getKey(ALIAS, null);
    }
    void save(Config config) throws Exception {
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding"); cipher.init(Cipher.ENCRYPT_MODE, key());
        cipher.updateAAD((config.base + "|" + config.device).getBytes(StandardCharsets.UTF_8));
        byte[] encrypted = cipher.doFinal(config.token.getBytes(StandardCharsets.US_ASCII));
        if (!prefs.edit().putString("base", config.base).putString("device", config.device)
            .putString("iv", Base64.encodeToString(cipher.getIV(), Base64.NO_WRAP))
            .putString("ciphertext", Base64.encodeToString(encrypted, Base64.NO_WRAP)).commit()) {
            throw new IllegalStateException("Secure settings could not be saved");
        }
    }
    Config load() throws Exception {
        if (!prefs.contains("ciphertext")) return null;
        String base = prefs.getString("base", ""), device = prefs.getString("device", "");
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.DECRYPT_MODE, key(), new GCMParameterSpec(128, Base64.decode(prefs.getString("iv", ""), Base64.NO_WRAP)));
        cipher.updateAAD((base + "|" + device).getBytes(StandardCharsets.UTF_8));
        byte[] token = cipher.doFinal(Base64.decode(prefs.getString("ciphertext", ""), Base64.NO_WRAP));
        try { return new Config(base, device, new String(token, StandardCharsets.US_ASCII)); }
        finally { java.util.Arrays.fill(token, (byte)0); }
    }
    void clear() { prefs.edit().clear().commit(); }
}
