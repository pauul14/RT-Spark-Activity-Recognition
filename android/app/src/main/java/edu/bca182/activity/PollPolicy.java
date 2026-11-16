package edu.bca182.activity;
/** Timing only; source timestamps and validation are never changed. */
public final class PollPolicy {
    public static final long CURRENT_MS=1000,HISTORY_MS=10000;
    private int failures; private long historyAt=Long.MIN_VALUE; private String historyState=""; private boolean blocked;
    public boolean historyDue(long now,String state){return historyAt==Long.MIN_VALUE||!historyState.equals(state)||now-historyAt>=HISTORY_MS;}
    public void historyReceived(long now,String state){historyAt=now;historyState=state;}
    public void succeeded(){failures=0;}
    public void failed(){failures=Math.min(failures+1,4);}
    public void blockAuthentication(){blocked=true;}
    public boolean authenticationBlocked(){return blocked;}
    public long nextDelayMs(){return Math.min(8000,CURRENT_MS<<Math.max(0,failures-1));}
}
