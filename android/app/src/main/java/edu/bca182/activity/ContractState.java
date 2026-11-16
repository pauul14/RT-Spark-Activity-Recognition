package edu.bca182.activity;

import java.net.URI;
import java.time.Instant;

/** Pure Java policy shared by views and JVM tests. No network or Android API. */
public final class ContractState {
    public static final String THREE_CLASS_MODEL_ID = "board-sha256:134446e8e056d45cb5cce01da0343e0d88e443aa55b2dc7843a15dfc06fa63eb:board_4s_v2_gravity_free";
    private ContractState() { }
    public static boolean compatible(int version, String model) {
        return version == 1 ? !THREE_CLASS_MODEL_ID.equals(model) : version == 2 && THREE_CLASS_MODEL_ID.equals(model);
    }
    public static String baseUrl(String value) {
        try {
            URI uri = new URI(value.trim());
            if (!"https".equalsIgnoreCase(uri.getScheme()) || uri.getHost() == null ||
                uri.getUserInfo() != null || uri.getQuery() != null || uri.getFragment() != null ||
                uri.getPort() == 0 || uri.getPort() < -1 || uri.getPort() > 65535 || uri.getHost().endsWith(".")) {
                throw new IllegalArgumentException("A trusted HTTPS API base URL is required");
            }
            String result = uri.toASCIIString();
            while (result.endsWith("/")) result = result.substring(0, result.length() - 1);
            return result;
        } catch (Exception failure) {
            throw new IllegalArgumentException("A trusted HTTPS API base URL is required");
        }
    }
    public static void credentials(String device, String token) {
        if (!device.matches("[A-Za-z0-9_-]{1,64}") || token.length() < 32 || token.length() > 512) {
            throw new IllegalArgumentException("Device ID or API token is invalid");
        }
        for (int i = 0; i < token.length(); ++i) {
            char c = token.charAt(i);
            if (c < 33 || c > 126) throw new IllegalArgumentException("API token must be printable ASCII without spaces");
        }
    }
    public static String display(String state, String activity, boolean validated,
                                 String mode, String timestamp, Instant now) {
        return display(state,activity,validated,mode,timestamp,now,1,"");
    }
    public static String display(String state,String activity,boolean validated,String mode,String timestamp,Instant now,int version,String model) {
        if (!compatible(version,model)) return "UNVALIDATED";
        if ("WALKING".equals(state) || "RUNNING".equals(state) || (version==2 && "STATIONARY".equals(state))) {
            if (!validated || !"board_validated".equals(mode)) return "UNVALIDATED";
            if (!state.toLowerCase(java.util.Locale.ROOT).equals(activity)) return "UNKNOWN";
            try {
                long age = now.toEpochMilli() - Instant.parse(timestamp).toEpochMilli();
                return age >= -5000 && age <= 20000 ? state : "OFFLINE";
            } catch (Exception failure) { return "UNKNOWN"; }
        }
        if ("UNVALIDATED".equals(state) || "WAITING".equals(state) || "OFFLINE".equals(state)) return state;
        return "UNKNOWN";
    }
    public static String historyLabel(String activity, boolean validated, String mode) {
        return historyLabel(activity,validated,mode,1,"");
    }
    public static String historyLabel(String activity,boolean validated,String mode,int version,String model) {
        if (!compatible(version,model) || !validated || !"board_validated".equals(mode)) return "UNVALIDATED";
        return "walking".equals(activity) || "running".equals(activity) || (version==2 && "stationary".equals(activity))
            ? activity.toUpperCase(java.util.Locale.ROOT) : "UNKNOWN";
    }
}
