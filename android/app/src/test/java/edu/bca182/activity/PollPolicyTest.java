package edu.bca182.activity;
import org.junit.Test;
import static org.junit.Assert.*;
public class PollPolicyTest {
    @Test public void currentIsOneSecondAndHistoryIsDeduplicated(){
        PollPolicy p=new PollPolicy();assertEquals(1000,p.nextDelayMs());assertTrue(p.historyDue(0,"WALKING"));
        p.historyReceived(0,"WALKING");for(int n=1;n<10;n++)assertFalse(p.historyDue(n*1000,"WALKING"));
        assertTrue(p.historyDue(10000,"WALKING"));assertTrue(p.historyDue(1000,"RUNNING"));
    }
    @Test public void reconnectBackoffIsBoundedAndResets(){PollPolicy p=new PollPolicy();for(int i=0;i<20;i++)p.failed();assertEquals(8000,p.nextDelayMs());p.succeeded();assertEquals(1000,p.nextDelayMs());}
    @Test public void authenticationRequiresExplicitRestart(){PollPolicy p=new PollPolicy();p.blockAuthentication();p.succeeded();assertTrue(p.authenticationBlocked());assertFalse(new PollPolicy().authenticationBlocked());}
    @Test public void failedHistoryIsNotMarkedFresh(){PollPolicy p=new PollPolicy();p.failed();assertTrue(p.historyDue(1,"WALKING"));}
}
