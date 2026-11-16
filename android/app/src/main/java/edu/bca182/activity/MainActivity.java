package edu.bca182.activity;

import android.app.Activity;
import android.app.AlertDialog;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.view.WindowManager;
import java.util.Collections;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import org.json.JSONArray;
import org.json.JSONObject;

/** Read-only monitor: no local classifier; versioned, source-bound activity contracts. */
public final class MainActivity extends Activity {
    private MonitorScreen screen;
    private SecretStore store;
    private volatile SecretStore.Config config;
    private volatile ApiClient active;
    private ScheduledExecutorService executor;
    private ScheduledFuture<?> scheduled;
    private PollPolicy policy=new PollPolicy();
    private final AtomicInteger generation=new AtomicInteger(),inFlight=new AtomicInteger(-1);
    private final Handler ui=new Handler(Looper.getMainLooper());
    private volatile boolean resumed;
    private JSONObject latestStatus;
    private boolean currentAvailable;
    private String renderedHistory="";
    @Override public void onCreate(Bundle saved) {
        super.onCreate(saved);
        getWindow().setFlags(WindowManager.LayoutParams.FLAG_SECURE, WindowManager.LayoutParams.FLAG_SECURE);
        screen = new MonitorScreen(this, this::requestRefresh, this::settings, this::forgetCredentials);
        setContentView(screen);
        store = new SecretStore(this);
        try {
            config = store.load();
        } catch (Exception e) {
            screen.showMessage("Secure settings unavailable. Re-enter API settings.");
        }
        if (config == null) screen.post(this::settings);
    }

    private void forgetCredentials() {
        stopPolling();
        store.clear();
        config = null;
        latestStatus = null;
        currentAvailable = false;
        clearHistory();
        screen.refresh.setEnabled(false);
        screen.showDevice(null);
        screen.showDemo(false);
        screen.showState("WAITING", "Setup required", "Credentials removed from app storage.", null);
    }

    private void clearHistory() {
        renderedHistory = "";
        screen.showLatest(null);
        screen.showHistory(Collections.emptyList());
    }

    @Override protected void onResume(){super.onResume();resumed=true;ui.post(freshness);startPolling();}
    @Override protected void onPause(){resumed=false;ui.removeCallbacks(freshness);stopPolling();super.onPause();}
    private final Runnable freshness=new Runnable(){public void run(){if(!resumed)return;if(currentAvailable&&latestStatus!=null&&config!=null)try{screen.showCurrent(ActivityPayload.parse(latestStatus,emptyHistory(latestStatus),config.device));}catch(Exception ignored){screen.showState("OFFLINE","No fresh telemetry","Waiting for a fresh device update.",null);}ui.postDelayed(this,250);}};
    private JSONObject emptyHistory(JSONObject status)throws Exception{JSONObject h=new JSONObject().put("items",new JSONArray());if(status.has("demo_scope"))h.put("demo_scope",status.getString("demo_scope"));return h;}
    private synchronized void schedule(int gen,long delay){if(executor!=null&&!executor.isShutdown()&&resumed&&gen==generation.get())scheduled=executor.schedule(()->fetch(gen),delay,TimeUnit.MILLISECONDS);}
    private void startPolling(){if(!resumed||config==null)return;stopPolling();policy=new PollPolicy();latestStatus=null;currentAvailable=false;renderedHistory="";screen.showDevice(config.device);screen.showState("LOADING","Connecting","Establishing the secure API connection…",null);executor=Executors.newSingleThreadScheduledExecutor();schedule(generation.get(),0);}
    private synchronized void stopPolling(){generation.incrementAndGet();inFlight.set(-1);if(scheduled!=null)scheduled.cancel(false);if(active!=null)active.cancel();if(executor!=null){executor.shutdownNow();executor=null;}}
    private synchronized void requestRefresh(){if(executor==null||!resumed||policy.authenticationBlocked()||inFlight.get()!=-1)return;if(scheduled!=null)scheduled.cancel(false);schedule(generation.get(),0);}
    private void fetch(int gen){
        if(!resumed||gen!=generation.get()||policy.authenticationBlocked()||!inFlight.compareAndSet(-1,gen))return;
        SecretStore.Config snapshot=config;final PollPolicy cyclePolicy=policy;ApiClient client=new ApiClient();active=client;boolean gotCurrent=false;
        runOnUiThread(()->{if(gen==generation.get())screen.refresh.setEnabled(false);});
        try{
            String device=ApiClient.deviceQuery(snapshot.device);JSONObject status=client.get(snapshot,"/api/activities/current?device_id="+device);
            ActivityPayload current=ActivityPayload.parse(status,emptyHistory(status),snapshot.device);gotCurrent=true;
            runOnUiThread(()->{if(gen==generation.get()&&resumed){latestStatus=status;currentAvailable=true;screen.showCurrent(current);}});
            if(cyclePolicy.historyDue(SystemClock.elapsedRealtime(),current.state)){
                JSONObject records=client.get(snapshot,"/api/activities/history?device_id="+device+"&limit=25");ActivityPayload full=ActivityPayload.parse(status,records,snapshot.device);cyclePolicy.historyReceived(SystemClock.elapsedRealtime(),current.state);
                runOnUiThread(()->{if(gen!=generation.get()||!resumed)return;screen.showLatest(full.latestPrediction);renderHistory(full);});
            }cyclePolicy.succeeded();
        }catch(Exception error){
            boolean auth=error instanceof ApiClient.ApiError&&((ApiClient.ApiError)error).authentication;if(auth)cyclePolicy.blockAuthentication();else cyclePolicy.failed();
            final boolean currentOK=gotCurrent&&!auth;final String safe=error instanceof ApiClient.ApiError?((ApiClient.ApiError)error).userMessage:"Private connection interrupted. Retrying securely.";
            runOnUiThread(() -> {
                if (gen != generation.get() || !resumed) return;
                if (currentOK) {
                    screen.showMessage("Current status received. History temporarily unavailable.");
                } else {
                    currentAvailable = false;
                    screen.showState("OFFLINE", auth ? "Authentication required" : "Reconnecting", safe, null);
                }
            });
        }finally{client.cancel();if(active==client)active=null;inFlight.compareAndSet(gen,-1);runOnUiThread(()->{if(gen==generation.get())screen.refresh.setEnabled(!cyclePolicy.authenticationBlocked());});if(!cyclePolicy.authenticationBlocked())schedule(gen,cyclePolicy.nextDelayMs());}
    }
    private void renderHistory(ActivityPayload payload) {
        String signature = payload.history.toString();
        if (signature.equals(renderedHistory)) return;
        renderedHistory = signature;
        screen.showHistory(payload.history);
    }

    private void settings() {
        stopPolling();
        currentAvailable = false;
        if (config != null) screen.showState("WAITING", "Updates paused", "Secure API settings are open.", null);
        MonitorScreen.SettingsForm form = new MonitorScreen.SettingsForm(this, config);
        AlertDialog dialog = form.dialog(this);
        dialog.setOnShowListener(x -> dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(v -> {
            try {
                SecretStore.Config next = new SecretStore.Config(form.endpoint.getText().toString(),
                        form.device.getText().toString().trim(), form.token.getText().toString());
                store.save(next);
                if (config == null || !config.device.equals(next.device) || !config.base.equals(next.base)) {
                    clearHistory();
                    screen.showDemo(false);
                }
                config = next;
                form.token.setText("");
                dialog.dismiss();
            } catch (Exception e) {
                form.message.setText("Settings could not be saved securely. Check HTTPS, device ID and token format.");
            }
        }));
        dialog.setOnDismissListener(x -> {
            form.token.setText("");
            if (resumed) startPolling();
        });
        dialog.show();
    }
}
