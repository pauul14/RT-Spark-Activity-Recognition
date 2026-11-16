package edu.bca182.activity;

import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import org.json.JSONArray;
import org.json.JSONObject;

final class ActivityPayload {
    final String state, timestamp, latestPrediction;
    final List<String> history;
    final boolean synthetic;
    private ActivityPayload(String state, String timestamp, String latestPrediction, List<String> history, boolean synthetic) {
        this.state = state; this.timestamp = timestamp; this.latestPrediction = latestPrediction; this.history = history;
        this.synthetic = synthetic;
    }
    private static boolean flag(JSONObject object) throws Exception {
        Object value = object.get("hardware_validated");
        if (!(value instanceof Boolean)) throw new IllegalArgumentException("Invalid validation flag");
        return (Boolean)value;
    }
    private static String text(JSONObject object, String key, int max) throws Exception {
        Object value = object.get(key);
        if (!(value instanceof String) || ((String)value).length() > max) throw new IllegalArgumentException("Invalid API field");
        return (String)value;
    }
    private static int version(JSONObject object) throws Exception {
        if (!object.has("prediction_schema_version")) return 1;
        Object v=object.get("prediction_schema_version");
        if (!(v instanceof Integer)) throw new IllegalArgumentException("Invalid prediction version");
        return (Integer)v;
    }
    private static String model(JSONObject object) throws Exception {
        return object.has("model_id") ? text(object,"model_id",128) : "";
    }
    static ActivityPayload parse(JSONObject current, JSONObject history, String device) throws Exception {
        // The fixture marks both responses; the bridge preserves these fields verbatim.
        // This label never grants validation or changes the production state policy.
        String currentScope = current.has("demo_scope") ? text(current, "demo_scope", 32) : "";
        String historyScope = history.has("demo_scope") ? text(history, "demo_scope", 32) : "";
        if (!currentScope.equals(historyScope) || (!currentScope.isEmpty() && !"SYNTHETIC_EMULATOR_ONLY".equals(currentScope))) {
            throw new IllegalArgumentException("Inconsistent demonstration scope");
        }
        boolean synthetic = !currentScope.isEmpty();
        if (!device.equals(text(current, "device_id", 64))) throw new IllegalArgumentException("Wrong device response");
        String stamp = current.isNull("server_timestamp") ? null : text(current, "server_timestamp", 64);
        String activity = current.isNull("activity") ? null : text(current, "activity", 16);
        String mode = current.has("mode") ? text(current, "mode", 32) : "";
        String state = ContractState.display(text(current, "state", 32), activity, flag(current), mode, stamp, Instant.now(), version(current), model(current));
        JSONArray items = history.getJSONArray("items");
        if (items.length() > 25) throw new IllegalArgumentException("History limit exceeded");
        List<String> rendered = new ArrayList<>();
        String latest = null; // the API returns newest first
        for (int index = 0; index < items.length(); ++index) {
            JSONObject item = items.getJSONObject(index);
            if (!device.equals(text(item, "device_id", 64))) throw new IllegalArgumentException("Wrong history device");
            String time = text(item, "server_timestamp", 64); Instant.parse(time);
            if (latest == null) latest = time;
            rendered.add(time + "\n" + ContractState.historyLabel(text(item, "activity", 16), flag(item), text(item, "inference_mode", 32), version(item), model(item)));
        }
        return new ActivityPayload(state, stamp, latest, rendered, synthetic);
    }
}
