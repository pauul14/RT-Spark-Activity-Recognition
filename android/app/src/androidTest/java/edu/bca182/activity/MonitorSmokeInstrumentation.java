package edu.bca182.activity;

import android.app.Activity;
import android.app.Instrumentation;
import android.content.Intent;
import android.os.Bundle;
import android.view.View;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.TextView;
import java.util.ArrayList;
import java.util.List;

/** Actual emulator UI/Keystore/HTTPS checks, exclusively against synthetic fixtures.
 * Never connects to a broker, cloud resource or RT-Spark. The validation flags in
 * positive fixtures exercise rendering, not model approval or hardware accuracy.
 */
public final class MonitorSmokeInstrumentation extends Instrumentation {
    private Bundle arguments;
    private MainActivity activity;
    private SecretStore store;
    private SecretStore.Config config;
    private SecretStore.Config controls;
    private final List<String> passed = new ArrayList<>();
    @Override public void onCreate(Bundle args) { arguments=args; super.onCreate(args); start(); }
    private List<String> texts() {
        List<String> result=new ArrayList<>();
        runOnMainSync(() -> collect(activity.getWindow().getDecorView(),result));
        return result;
    }
    private void collect(View view,List<String> result) {
        if(view instanceof TextView)result.add(((TextView)view).getText().toString());
        if(view instanceof ViewGroup)for(int i=0;i<((ViewGroup)view).getChildCount();i++)collect(((ViewGroup)view).getChildAt(i),result);
    }
    private Button findButton(View view,String label) {
        if(view instanceof Button && label.contentEquals(((Button)view).getText()))return (Button)view;
        if(view instanceof ViewGroup)for(int i=0;i<((ViewGroup)view).getChildCount();i++) {
            Button found=findButton(((ViewGroup)view).getChildAt(i),label);if(found!=null)return found;
        }
        return null;
    }
    private void refresh() { runOnMainSync(() -> {Button b=findButton(activity.getWindow().getDecorView(),"Refresh");if(b!=null)b.performClick();}); }
    private void expect(String value) throws Exception {
        long deadline=android.os.SystemClock.elapsedRealtime()+15000;
        while(android.os.SystemClock.elapsedRealtime()<deadline) {
            if(texts().contains(value))return;
            Thread.sleep(100);
        }
        throw new AssertionError("Expected synthetic UI state missing: "+value);
    }
    private void expectStartup() throws Exception {
        long deadline=android.os.SystemClock.elapsedRealtime()+15000;
        while(android.os.SystemClock.elapsedRealtime()<deadline) {
            List<String> values=texts();
            if(values.contains("LOADING") || values.contains("UNVALIDATED"))return;
            Thread.sleep(100);
        }
        throw new AssertionError("Startup did not reach loading or completed synthetic response");
    }
    private void mark(String name) { passed.add(name);Bundle progress=new Bundle();progress.putString("stream","SYNTHETIC "+name+" PASS\n");sendStatus(0,progress); }
    private void scenario(String name) throws Exception { new ApiClient().get(controls,"/__test__/scenario?mode="+name); }
    private void capture(String name) {
        runOnMainSync(()->{
            View view=activity.getWindow().getDecorView();
            saveImage(view,name+".png");
            android.widget.ScrollView scroll=findScroll(view);
            if(scroll!=null)saveImage(scroll.getChildAt(0),name+"-full.png");
        });
    }
    private android.widget.ScrollView findScroll(View v){
        if(v instanceof android.widget.ScrollView)return (android.widget.ScrollView)v;
        if(v instanceof ViewGroup)for(int i=0;i<((ViewGroup)v).getChildCount();i++){android.widget.ScrollView s=findScroll(((ViewGroup)v).getChildAt(i));if(s!=null)return s;}return null;
    }
    private void saveImage(View view,String name){
        if(view.getWidth()<1||view.getHeight()<1||view.getHeight()>6000)throw new AssertionError("Invalid synthetic capture bounds");
        android.graphics.Bitmap image=android.graphics.Bitmap.createBitmap(view.getWidth(),view.getHeight(),android.graphics.Bitmap.Config.ARGB_8888);
        android.graphics.Canvas canvas=new android.graphics.Canvas(image);
        boolean dark=(activity.getResources().getConfiguration().uiMode&android.content.res.Configuration.UI_MODE_NIGHT_MASK)==android.content.res.Configuration.UI_MODE_NIGHT_YES;
        canvas.drawColor(dark?0xff0d0f13:0xfff5f5f7);view.draw(canvas);
        try(java.io.FileOutputStream out=new java.io.FileOutputStream(new java.io.File(getTargetContext().getFilesDir(),name))){image.compress(android.graphics.Bitmap.CompressFormat.PNG,100,out);}catch(Exception e){throw new RuntimeException(e);}finally{image.recycle();}
    }
    @Override public void onStart() {
        Bundle result=new Bundle();
        int resultCode=Activity.RESULT_CANCELED;
        try {
            String base=arguments.getString("fixture_url");
            if(base==null || !base.startsWith("https://10.0.2.2:"))throw new IllegalArgumentException("Owned emulator fixture required");
            config=new SecretStore.Config(base,"dataset-demo","synthetic-emulator-only-token-0123456789");
            String controlBase=arguments.getString("control_url",base);
            if(!controlBase.matches("https://10\\.0\\.2\\.2:[0-9]{1,5}"))throw new IllegalArgumentException("Owned emulator controls required");
            controls=new SecretStore.Config(controlBase,config.device,config.token);
            store=new SecretStore(getTargetContext());store.clear();store.save(config);
            if(!config.token.equals(store.load().token))throw new AssertionError("Keystore round trip failed");
            mark("keystore_round_trip");
            if("true".equals(arguments.getString("visual_only"))){
                String mode=arguments.getString("mode","walking");scenario(mode);
                Intent visual=new Intent(getTargetContext(),MainActivity.class);visual.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                activity=(MainActivity)startActivitySync(visual);
                expect("walking".equals(mode)?"WALKING":"running".equals(mode)?"RUNNING":"stationary".equals(mode)?"STATIONARY":"stale".equals(mode)?"OFFLINE":"waiting".equals(mode)?"WAITING":"UNVALIDATED");
                Thread.sleep(500);capture("synthetic-visual");
                result.putString("stream","PASS: synthetic visual capture\n");resultCode=Activity.RESULT_OK;return;
            }
            scenario("slow");
            Intent intent=new Intent(getTargetContext(),MainActivity.class);intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            activity=(MainActivity)startActivitySync(intent);expectStartup();mark("startup_loading_or_completed");
            expect("UNVALIDATED");mark("unvalidated_messaging");
            expect("SYNTHETIC DEMO — hardware and model unvalidated");mark("visible_synthetic_scope");
            scenario("walking");refresh();expect("WALKING");mark("walking_render_fixture");
            if(texts().stream().noneMatch(t->t.startsWith("Latest prediction: ") && !t.endsWith("none")))throw new AssertionError("Latest timestamp missing");
            if(!texts().contains("Activity history"))throw new AssertionError("History heading missing");
            long historyDeadline=android.os.SystemClock.elapsedRealtime()+5000;
            while(texts().stream().noneMatch(t->t.contains("\nWALKING")) && android.os.SystemClock.elapsedRealtime()<historyDeadline)Thread.sleep(100);
            if(texts().stream().noneMatch(t->t.contains("\nWALKING")))throw new AssertionError("History row missing");
            mark("latest_timestamp_and_history");
            scenario("running");refresh();expect("RUNNING");mark("running_and_refresh_fixture");
            scenario("stationary");refresh();expect("STATIONARY");mark("stationary_v2_render_fixture");
            long stationaryDeadline=android.os.SystemClock.elapsedRealtime()+5000;
            while(texts().stream().noneMatch(t->t.contains("\nSTATIONARY")) && android.os.SystemClock.elapsedRealtime()<stationaryDeadline)Thread.sleep(100);
            if(texts().stream().noneMatch(t->t.contains("\nSTATIONARY")))throw new AssertionError("Stationary history row missing");
            mark("stationary_v2_history_fixture");
            scenario("stale");refresh();expect("OFFLINE");mark("stale_prediction_gate");
            scenario("hardware");refresh();expect("UNVALIDATED");
            expect("No activity history yet.");mark("production_hardware_gate");
            scenario("waiting");refresh();expect("WAITING");mark("waiting");
            scenario("error");refresh();expect("OFFLINE");mark("error_state");
            scenario("unvalidated");refresh();expect("UNVALIDATED");mark("error_recovery");
            // Measure server-fixture-change to visible UI, without manual Refresh.
            // This does not measure FIR, model recognition or real cloud latency.
            StringBuilder timing=new StringBuilder();
            for(int i=0;i<6;i++){
                String mode=new String[]{"walking","running","stationary"}[i%3];
                long began=android.os.SystemClock.elapsedRealtime();scenario(mode);expect(mode.toUpperCase(java.util.Locale.ROOT));
                if(i>0)timing.append(',');timing.append(android.os.SystemClock.elapsedRealtime()-began);
            }
            result.putString("synthetic_display_latency_ms",timing.toString());mark("automatic_transition_latency");
            Bundle measured=new Bundle();measured.putString("stream","synthetic_display_latency_ms="+timing+"\n");sendStatus(0,measured);
            org.json.JSONObject before=new ApiClient().get(controls,"/__test__/stats");Thread.sleep(6200);
            org.json.JSONObject after=new ApiClient().get(controls,"/__test__/stats");
            int currentCount=after.getInt("current")-before.getInt("current"),historyCount=after.getInt("history")-before.getInt("history");
            if(currentCount<3||currentCount>8||historyCount>1)throw new AssertionError("Polling request budget failed");
            result.putString("six_second_request_counts",currentCount+" current, "+historyCount+" history");mark("deduplicated_history_polling");
            Bundle budget=new Bundle();budget.putString("stream","six_second_request_counts="+currentCount+" current, "+historyCount+" history\n");sendStatus(0,budget);
            scenario("error");expect("OFFLINE");scenario("walking");expect("WALKING");mark("automatic_tunnel_style_recovery");
            runOnMainSync(()->{
                View view=activity.getWindow().getDecorView();
                android.graphics.Bitmap image=android.graphics.Bitmap.createBitmap(view.getWidth(),view.getHeight(),android.graphics.Bitmap.Config.ARGB_8888);
                view.draw(new android.graphics.Canvas(image));
                try(java.io.FileOutputStream out=new java.io.FileOutputStream(new java.io.File(getTargetContext().getFilesDir(),"synthetic-ui.png"))){image.compress(android.graphics.Bitmap.CompressFormat.PNG,100,out);}catch(Exception e){throw new RuntimeException(e);}finally{image.recycle();}
            });
            SecretStore.Config wrong=new SecretStore.Config(config.base,config.device,"synthetic-wrong-token-01234567890123456789");
            try {
                new ApiClient().get(wrong,"/api/activities/current?device_id=dataset-demo");
                throw new AssertionError("Invalid API token accepted");
            } catch(ApiClient.ApiError expected) {
                if(!expected.userMessage.startsWith("Authentication failed"))throw expected;
            }
            mark("invalid_token_rejected");
            scenario("frozen");expect("WALKING");Thread.sleep(21000);expect("OFFLINE");mark("local_source_timestamp_expiry");
            scenario("unauthorized");expect("\u25cf  Authentication required");
            int stopped=new ApiClient().get(controls,"/__test__/stats").getInt("current");Thread.sleep(3000);
            if(new ApiClient().get(controls,"/__test__/stats").getInt("current")!=stopped)throw new AssertionError("Authentication failure retried automatically");
            mark("authentication_stops_polling");
            scenario("walking");runOnMainSync(()->activity.finish());
            activity=(MainActivity)startActivitySync(intent);expect("WALKING");mark("explicit_restart_recovers_after_authentication");
            result.putString("stream","PASS: "+passed.size()+" synthetic emulator cases; no physical/cloud/model validation.\n");
            result.putInt("cases_passed",passed.size());
            resultCode=Activity.RESULT_OK;
        } catch(Throwable failure) {
            String classes=failure.getClass().getSimpleName();Throwable cause=failure.getCause();for(int i=0;cause!=null&&i<4;i++,cause=cause.getCause())classes+="/"+cause.getClass().getSimpleName();
            String safe=failure instanceof ApiClient.ApiError?((ApiClient.ApiError)failure).userMessage:failure instanceof AssertionError?failure.getMessage():"";
            result.putString("stream","FAIL: synthetic emulator check "+classes+" after "+passed.size()+" cases. "+safe+"\n");
            result.putString("error_type",failure.getClass().getSimpleName());result.putInt("cases_passed",passed.size());
        } finally {
            boolean keepDemo=resultCode==Activity.RESULT_OK && "true".equals(arguments.getString("keep_demo"));
            if(activity!=null)runOnMainSync(()->activity.finish());
            if(store!=null && !keepDemo)store.clear();
            // Visual-only returns still finish instrumentation through this finally.
            finish(resultCode,result);
        }
    }
}
