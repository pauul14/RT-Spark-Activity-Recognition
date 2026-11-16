package edu.bca182.activity;
import org.junit.Test;
import static org.junit.Assert.*;
import java.time.Instant;
public class ThreeClassContractTest {
    private final Instant now=Instant.parse("2026-11-07T00:00:00Z");
    @Test public void stationaryRequiresV2ExactModelAndValidation() {
        String m=ContractState.THREE_CLASS_MODEL_ID;
        assertEquals("STATIONARY",ContractState.display("STATIONARY","stationary",true,"board_validated",now.toString(),now,2,m));
        assertEquals("UNKNOWN",ContractState.display("STATIONARY","stationary",true,"board_validated",now.toString(),now));
        assertEquals("UNVALIDATED",ContractState.display("STATIONARY","stationary",true,"board_validated",now.toString(),now,2,"wrong"));
        assertEquals("UNVALIDATED",ContractState.display("STATIONARY","stationary",false,"board_validated",now.toString(),now,2,m));
        assertEquals("UNVALIDATED",ContractState.display("WALKING","walking",true,"board_validated",now.toString(),now,1,m));
    }
    @Test public void v2ExpiryContradictionAndHistory() {
        String m=ContractState.THREE_CLASS_MODEL_ID;
        assertEquals("OFFLINE",ContractState.display("STATIONARY","stationary",true,"board_validated",now.minusSeconds(21).toString(),now,2,m));
        assertEquals("UNKNOWN",ContractState.display("STATIONARY","walking",true,"board_validated",now.toString(),now,2,m));
        assertEquals("STATIONARY",ContractState.historyLabel("stationary",true,"board_validated",2,m));
        assertEquals("UNVALIDATED",ContractState.historyLabel("stationary",true,"dataset_demo",2,m));
        assertEquals("UNKNOWN",ContractState.historyLabel("stationary",true,"board_validated"));
    }
}
