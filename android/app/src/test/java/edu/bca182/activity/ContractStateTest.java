package edu.bca182.activity;
import org.junit.Test;
import static org.junit.Assert.*;
import java.time.Instant;

public class ContractStateTest {
    private final Instant now = Instant.parse("2026-11-01T12:00:00Z");
    @Test public void onlyReviewedFreshBoardResultShowsActivity() {
        assertEquals("WALKING", ContractState.display("WALKING", "walking", true, "board_validated", "2026-11-01T11:59:59Z", now));
        assertEquals("UNVALIDATED", ContractState.display("WALKING", "walking", false, "dataset_demo", "2026-11-01T11:59:59Z", now));
        assertEquals("UNVALIDATED", ContractState.display("RUNNING", "running", true, "dataset_demo", "2026-11-01T11:59:59Z", now));
    }
    @Test public void staleFutureAndContradictoryResultsFailClosed() {
        assertEquals("OFFLINE", ContractState.display("RUNNING", "running", true, "board_validated", "2026-11-01T11:59:00Z", now));
        assertEquals("OFFLINE", ContractState.display("RUNNING", "running", true, "board_validated", "2026-11-01T12:01:00Z", now));
        assertEquals("UNKNOWN", ContractState.display("RUNNING", "walking", true, "board_validated", "2026-11-01T12:00:00Z", now));
        assertEquals("UNKNOWN", ContractState.display("RUNNING", "running", true, "board_validated", "invalid", now));
    }
    @Test public void waitingOfflineAndUnknownRemainExplicit() {
        assertEquals("WAITING", ContractState.display("WAITING", null, false, "", null, now));
        assertEquals("OFFLINE", ContractState.display("OFFLINE", null, false, "", null, now));
        assertEquals("UNKNOWN", ContractState.display("invented", null, false, "", null, now));
    }
    @Test public void historyDoesNotPresentDemoClassAsHardwareActivity() {
        assertEquals("UNVALIDATED", ContractState.historyLabel("running", false, "dataset_demo"));
        assertEquals("RUNNING", ContractState.historyLabel("running", true, "board_validated"));
        assertEquals("UNKNOWN", ContractState.historyLabel("invented", true, "board_validated"));
    }
    @Test public void endpointRejectsPlaintextAndCredentialBearingUrls() {
        assertEquals("https://api.example.test", ContractState.baseUrl("https://api.example.test/"));
        for (String bad : new String[]{"http://api.example.test", "https://user:secret@api.example.test", "https://api.example.test/?token=secret", "https://api.example.test/#secret", "file:///secret"}) {
            try { ContractState.baseUrl(bad); fail("Unsafe endpoint admitted"); } catch (IllegalArgumentException expected) { }
        }
    }
    @Test public void tokenRejectsHeaderInjectionAndBadDeviceIds() {
        ContractState.credentials("rtspark-123", "synthetic-unit-test-token-0123456789");
        for (String token : new String[]{"short", "long-token-with-space " + "x".repeat(32), "x".repeat(32) + "\r\n"}) {
            try { ContractState.credentials("device", token); fail("Unsafe token admitted"); } catch (IllegalArgumentException expected) { }
        }
        try { ContractState.credentials("device/other", "x".repeat(32)); fail(); } catch (IllegalArgumentException expected) { }
    }
}
