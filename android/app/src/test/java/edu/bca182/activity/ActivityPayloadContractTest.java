package edu.bca182.activity;
import org.json.JSONObject;
import org.junit.Test;
import static org.junit.Assert.*;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;

/** Parses real backend responses (synthetic data) saved by backend/tests/test_android_contract.py. */
public class ActivityPayloadContractTest {
    private static JSONObject fixture(String name) throws Exception {
        try (InputStream input = ActivityPayloadContractTest.class.getResourceAsStream("/contract/" + name + ".json")) {
            return new JSONObject(new String(input.readAllBytes(), StandardCharsets.UTF_8));
        }
    }
    private static ActivityPayload parse(String name, String device) throws Exception {
        JSONObject saved = fixture(name);
        return ActivityPayload.parse(saved.getJSONObject("current"), saved.getJSONObject("history"), device);
    }
    @Test public void waitingHasNoTimestampsOrHistory() throws Exception {
        ActivityPayload payload = parse("waiting", "dataset-demo");
        assertEquals("WAITING", payload.state);
        assertNull(payload.timestamp); assertNull(payload.latestPrediction);
        assertTrue(payload.history.isEmpty());
    }
    @Test public void datasetResultsStayUnvalidated() throws Exception {
        ActivityPayload payload = parse("dataset_demo", "dataset-demo");
        assertEquals("UNVALIDATED", payload.state);
        assertFalse(payload.history.isEmpty());
        for (String row : payload.history) assertTrue(row.endsWith("\nUNVALIDATED"));
    }
    @Test public void savedValidatedResultIsStaleSoFailsClosed() throws Exception {
        ActivityPayload payload = parse("board_validated", "dataset-demo");
        assertEquals("OFFLINE", payload.state); // the fixture is older than 20 s
        assertEquals("WALKING", payload.history.get(0).split("\n")[1]);
        JSONObject first = fixture("board_validated").getJSONObject("history").getJSONArray("items").getJSONObject(0);
        assertEquals(first.getString("server_timestamp"), payload.latestPrediction);
    }
    @Test public void responseForAnotherDeviceIsRejected() throws Exception {
        try { parse("dataset_demo", "other-device"); fail("Wrong device admitted"); }
        catch (IllegalArgumentException expected) { }
    }
    @Test public void syntheticScopeIsExplicitAndDoesNotGrantValidation() throws Exception {
        JSONObject saved = fixture("dataset_demo");
        saved.getJSONObject("current").put("demo_scope", "SYNTHETIC_EMULATOR_ONLY");
        saved.getJSONObject("history").put("demo_scope", "SYNTHETIC_EMULATOR_ONLY");
        ActivityPayload payload = ActivityPayload.parse(saved.getJSONObject("current"), saved.getJSONObject("history"), "dataset-demo");
        assertTrue(payload.synthetic);
        assertEquals("UNVALIDATED", payload.state);
        assertFalse(parse("dataset_demo", "dataset-demo").synthetic);
    }
    @Test public void mixedSyntheticAndLiveResponsesFailClosed() throws Exception {
        JSONObject saved = fixture("dataset_demo");
        saved.getJSONObject("current").put("demo_scope", "SYNTHETIC_EMULATOR_ONLY");
        try {
            ActivityPayload.parse(saved.getJSONObject("current"), saved.getJSONObject("history"), "dataset-demo");
            fail("Mixed scope admitted");
        } catch (IllegalArgumentException expected) { }
    }
}
