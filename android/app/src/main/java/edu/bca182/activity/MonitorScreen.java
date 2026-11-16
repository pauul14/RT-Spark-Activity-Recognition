package edu.bca182.activity;

import android.app.AlertDialog;
import android.content.Context;
import android.content.res.ColorStateList;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.RadialGradient;
import android.graphics.Shader;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.graphics.drawable.RippleDrawable;
import android.text.InputType;
import android.text.SpannableString;
import android.text.Spanned;
import android.text.style.ForegroundColorSpan;
import android.text.style.RelativeSizeSpan;
import android.text.style.StyleSpan;
import android.view.Gravity;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import java.util.List;

/** The monitor's native views. Polling and validation remain outside this class. */
final class MonitorScreen extends ScrollView {
    static final int BACKGROUND = 0xff080b11;
    private static final int INK = 0xfff6f8fc;
    private static final int MUTED = 0xff9babbf;
    private static final int BORDER = 0xff293343;
    private static final int PANEL = 0xff111720;
    private static final int CENTER = Gravity.CENTER_HORIZONTAL;

    private final TextView state, connection, message, demo, device, updated, predicted;
    private final LinearLayout history;
    private final ActivityMark mark;
    final Button refresh;

    MonitorScreen(Context context, Runnable refreshAction, Runnable settings, Runnable forget) {
        super(context);
        setFillViewport(true);
        setBackgroundColor(BACKGROUND);
        LinearLayout root = column(context);
        root.setPadding(dp(context, 20), dp(context, 18), dp(context, 20), dp(context, 24));
        root.setOnApplyWindowInsetsListener((view, insets) -> {
            view.setPadding(dp(context, 20) + insets.getSystemWindowInsetLeft(),
                    dp(context, 18) + insets.getSystemWindowInsetTop(),
                    dp(context, 20) + insets.getSystemWindowInsetRight(),
                    dp(context, 24) + insets.getSystemWindowInsetBottom());
            return insets;
        });
        addView(root);
        caption(root, "RT-SPARK  /  BCA182", 0);
        label(root, "Activity monitor", 33, INK, true, 0, 4);
        label(root, "Front sling bag · Limited prototype", 13, MUTED, false, 0, 2);

        demo = label(root, "", 13, Tone.WAITING.accent, true, 0, 10);
        demo.setVisibility(GONE);
        LinearLayout hero = panel(root, 14, 20, 28);
        GradientDrawable heroSurface = new GradientDrawable(GradientDrawable.Orientation.TOP_BOTTOM,
                new int[]{0xff151e2b, PANEL, 0xff10151e});
        heroSurface.setCornerRadius(dp(context, 28));
        heroSurface.setStroke(dp(context, 1), BORDER);
        hero.setBackground(heroSurface);
        hero.setGravity(CENTER);
        caption(hero, "CURRENT PREDICTION", CENTER);
        mark = new ActivityMark(context);
        hero.addView(mark, new LinearLayout.LayoutParams(dp(context, 146), dp(context, 146)));
        state = label(hero, "", 32, INK, true, CENTER, 0);
        state.setAccessibilityLiveRegion(ACCESSIBILITY_LIVE_REGION_POLITE);
        message = label(hero, "", 14, MUTED, false, Gravity.CENTER, 6);

        LinearLayout details = panel(root, 12, 16, 20);
        caption(details, "DEVICE STATUS", 0);
        connection = label(details, "", 15, MUTED, true, 0, 6);
        device = label(details, "", 12, MUTED, false, 0, 5);
        updated = label(details, "", 12, MUTED, false, 0, 3);
        predicted = label(details, "", 12, MUTED, false, 0, 3);

        LinearLayout actions = new LinearLayout(context);
        root.addView(actions);
        refresh = action(actions, "Refresh", refreshAction, 0);
        refresh.setEnabled(false);
        action(actions, "API settings", settings, 8);
        label(root, "Activity history", 24, INK, true, 0, 14);
        label(root, "Previous predictions, most recent first.", 13, MUTED, false, 0, 3);
        history = column(context);
        root.addView(history);
        LinearLayout footer = new LinearLayout(context);
        root.addView(footer);
        action(footer, "Forget API credentials", forget, 0);

        showState("WAITING", "Setup required", "Configure an HTTPS API to begin.", null);
        showDevice(null);
        showLatest(null);
        showHistory(java.util.Collections.emptyList());
    }

    void showCurrent(ActivityPayload payload) {
        String prediction = payload.state;
        Tone tone = Tone.forState(prediction);
        if (tone.activity) {
            showState(prediction, "Live · Secure API", "Automatically updated by RT-Spark.", payload.timestamp);
        } else if ("OFFLINE".equals(prediction)) {
            showState(prediction, "No fresh telemetry", "Waiting for a fresh device update.", payload.timestamp);
        } else if ("WAITING".equals(prediction)) {
            showState(prediction, "Secure API connected", "Waiting for device telemetry.", payload.timestamp);
        } else {
            showState(prediction, "Secure API connected", "A validated activity is unavailable.", payload.timestamp);
        }
        showDemo(payload.synthetic);
    }

    void showState(String value, String link, String description, String timestamp) {
        Tone tone = Tone.forState(value);
        if (setIfChanged(state, value)) {
            state.setTextSize(value.length() >= 10 ? 28 : 34);
            mark.setTone(tone);
        }
        if (setIfChanged(connection, link)) connection.setTextColor(tone.activity ? Tone.STATIONARY.accent : tone.accent);
        setIfChanged(message, description);
        setIfChanged(updated, "Last update: " + (timestamp == null ? "none" : timestamp));
    }

    void showMessage(String value) { setIfChanged(message, value); }
    void showDevice(String value) { setIfChanged(device, "Device: " + (value == null ? "Not configured" : value)); }
    void showLatest(String value) { setIfChanged(predicted, "Latest prediction: " + (value == null ? "none" : value)); }
    void showDemo(boolean synthetic) {
        setIfChanged(demo, "SYNTHETIC DEMO — hardware and model unvalidated");
        demo.setVisibility(synthetic ? VISIBLE : GONE);
    }

    void showHistory(List<String> records) {
        history.removeAllViews();
        if (records.isEmpty()) {
            label(history, "No activity history yet.", 14, MUTED, false, 0, 10);
            return;
        }
        for (String record : records) {
            int split = record.indexOf('\n');
            LinearLayout item = panel(history, 8, 12, 16);
            TextView row = label(item, "", 15, MUTED, false, 0, 0);
            SpannableString styled = new SpannableString(record);
            styled.setSpan(new RelativeSizeSpan(.8f), 0, split, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
            styled.setSpan(new StyleSpan(Typeface.BOLD), split + 1, record.length(), Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
            styled.setSpan(new ForegroundColorSpan(Tone.forState(record.substring(split + 1)).accent),
                    split + 1, record.length(), Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
            row.setText(styled);
        }
    }

    private static boolean setIfChanged(TextView view, String value) {
        if (value.contentEquals(view.getText())) return false;
        view.setText(value);
        return true;
    }

    private static int dp(Context context, int n) {
        return Math.round(n * context.getResources().getDisplayMetrics().density);
    }

    private static LinearLayout column(Context context) {
        LinearLayout view = new LinearLayout(context);
        view.setOrientation(LinearLayout.VERTICAL);
        return view;
    }

    private static GradientDrawable surface(Context context, int color, int radius) {
        GradientDrawable drawable = new GradientDrawable();
        drawable.setColor(color);
        drawable.setCornerRadius(dp(context, radius));
        drawable.setStroke(dp(context, 1), BORDER);
        return drawable;
    }

    private static TextView label(LinearLayout parent, String value, int size, int color,
                                  boolean bold, int gravity, int top) {
        Context context = parent.getContext();
        TextView view = new TextView(context);
        view.setText(value);
        view.setTextSize(size);
        view.setTextColor(color);
        view.setTypeface(Typeface.create("sans-serif", bold ? Typeface.BOLD : Typeface.NORMAL));
        view.setGravity(gravity);
        view.setIncludeFontPadding(false);
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(-1, -2);
        params.topMargin = dp(context, top);
        parent.addView(view, params);
        return view;
    }

    private static void caption(LinearLayout parent, String value, int gravity) {
        TextView view = label(parent, value, 11, MUTED, true, gravity, 0);
        view.setLetterSpacing(.16f);
    }

    private static LinearLayout panel(LinearLayout parent, int margin, int padding, int radius) {
        Context context = parent.getContext();
        LinearLayout view = column(context);
        int inset = dp(context, padding);
        view.setPadding(inset, inset, inset, inset);
        view.setBackground(surface(context, PANEL, radius));
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(-1, -2);
        params.topMargin = dp(context, margin);
        parent.addView(view, params);
        return view;
    }

    private static Button action(LinearLayout parent, String title, Runnable listener, int margin) {
        Context context = parent.getContext();
        Button view = new Button(context);
        view.setText(title);
        view.setTextSize(14);
        view.setTextColor(new ColorStateList(new int[][]{{-android.R.attr.state_enabled}, {}},
                new int[]{MUTED, INK}));
        view.setAllCaps(false);
        view.setMinHeight(dp(context, 48));
        view.setBackground(new RippleDrawable(ColorStateList.valueOf(0x40a4c7ff),
                surface(context, PANEL, 15), null));
        view.setOnClickListener(v -> listener.run());
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(0, -2, 1);
        params.setMarginStart(dp(context, margin));
        params.topMargin = dp(context, 12);
        parent.addView(view, params);
        return view;
    }

    static final class SettingsForm {
        final LinearLayout fields;
        final EditText endpoint, device, token;
        final TextView message;

        SettingsForm(Context context, SecretStore.Config current) {
            fields = column(context);
            fields.setPadding(dp(context, 24), dp(context, 10), dp(context, 24), dp(context, 8));
            endpoint = field("HTTPS API base URL", current == null ? "" : current.base, InputType.TYPE_TEXT_VARIATION_URI);
            device = field("Device ID", current == null ? "" : current.device, InputType.TYPE_TEXT_VARIATION_NORMAL);
            token = field("API token (hidden; 32+ characters)", "", InputType.TYPE_TEXT_VARIATION_PASSWORD);
            message = label(fields, "Use the application's API token, never a GitHub or Wi-Fi password.",
                    13, MUTED, false, 0, 10);
        }

        AlertDialog dialog(Context context) {
            return new AlertDialog.Builder(context, android.R.style.Theme_Material_Dialog_Alert)
                    .setTitle("Secure API settings").setView(fields)
                    .setNegativeButton("Cancel", null).setPositiveButton("Save", null).create();
        }

        private EditText field(String hint, String value, int variation) {
            EditText view = new EditText(fields.getContext());
            view.setHint(hint);
            view.setSingleLine(true);
            view.setInputType(InputType.TYPE_CLASS_TEXT | variation);
            view.setText(value);
            view.setTextSize(15);
            view.setTextColor(INK);
            view.setHintTextColor(MUTED);
            view.setBackgroundTintList(ColorStateList.valueOf(BORDER));
            view.setSaveEnabled(false);
            view.setImportantForAutofill(IMPORTANT_FOR_AUTOFILL_NO_EXCLUDE_DESCENDANTS);
            fields.addView(view, new LinearLayout.LayoutParams(-1, -2));
            return view;
        }
    }

    private enum Tone {
        WALKING(0xff58aeff, true), RUNNING(0xffff8556, true), STATIONARY(0xff53e1a5, true),
        OFFLINE(MUTED, false), CONNECTING(0xffc1a7ff, false), WAITING(0xffffd277, false),
        INVALID(0xffffd277, false);

        final int accent;
        final boolean activity;

        Tone(int accent, boolean activity) { this.accent = accent; this.activity = activity; }

        static Tone forState(String state) {
            switch (state) {
                case "WALKING": return WALKING;
                case "RUNNING": return RUNNING;
                case "STATIONARY": return STATIONARY;
                case "OFFLINE": return OFFLINE;
                case "LOADING": return CONNECTING;
                case "WAITING": return WAITING;
                default: return INVALID;
            }
        }
    }

    /** Fixed rings and a glyph drawn in the same 64-unit coordinates as the LCD art. */
    private static final class ActivityMark extends View {
        private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
        private final Path glyph = new Path();
        private Tone tone = Tone.WAITING;
        private Shader glow;

        ActivityMark(Context context) {
            super(context);
            setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_NO);
            paint.setStrokeCap(Paint.Cap.ROUND);
            paint.setStrokeJoin(Paint.Join.ROUND);
            setTone(Tone.WAITING);
        }

        void setTone(Tone next) {
            if (next == tone && glow != null) return;
            tone = next;
            int rgb = tone.accent & 0xffffff;
            glow = new RadialGradient(0, 0, 71,
                    new int[]{rgb | 0x14000000, rgb | 0x28000000, rgb | 0x44000000, rgb},
                    new float[]{0, .55f, .75f, 1}, Shader.TileMode.CLAMP);
            glyph.reset();
            switch (tone) {
                case WALKING:
                    line(31,23, 29,38, 20,57); line(29,38, 41,47, 45,57);
                    line(19,36, 23,28, 32,22, 41,33, 49,34); break;
                case RUNNING:
                    line(26,25, 34,21, 42,24, 48,30, 55,26); line(40,24, 31,36, 44,43, 41,55);
                    line(31,36, 22,47, 10,49); break;
                case STATIONARY:
                    line(32,23, 32,41); line(20,33, 24,26, 32,23, 40,26, 44,33);
                    line(25,57, 32,41, 39,57); break;
                case OFFLINE:
                    line(11,45, 22,45, 28,35); line(36,29, 42,19, 53,19); line(15,15, 49,49); break;
                case CONNECTING:
                    line(27,21, 35,13, 43,13, 51,21, 51,29, 43,37);
                    line(21,27, 13,35, 13,43, 21,51, 29,51, 37,43); line(24,40, 40,24); break;
                case WAITING:
                    line(16,11, 48,11); line(16,53, 48,53); line(22,12, 22,21, 42,43, 42,52);
                    line(42,12, 42,21, 22,43, 22,52); break;
                case INVALID:
                    line(32,15, 32,36); break;
            }
            invalidate();
        }

        private void line(int... points) {
            glyph.moveTo(points[0], points[1]);
            for (int i = 2; i < points.length; i += 2) glyph.lineTo(points[i], points[i + 1]);
        }

        @Override protected void onDraw(Canvas canvas) {
            super.onDraw(canvas);
            float scale = Math.min(getWidth(), getHeight()) / 146f;
            canvas.save();
            canvas.translate(getWidth() / 2f, getHeight() / 2f);
            canvas.scale(scale, scale);
            paint.setStyle(Paint.Style.FILL);
            paint.setShader(glow);
            canvas.drawCircle(0, 0, 71, paint);
            paint.setShader(null);
            paint.setStyle(Paint.Style.STROKE);
            paint.setStrokeWidth(1);
            paint.setColor((tone.accent & 0xffffff) | 0x52000000);
            canvas.drawCircle(0, 0, 61, paint);
            paint.setColor(tone.accent);
            paint.setStrokeWidth(5);
            canvas.drawCircle(0, 0, 53, paint);
            canvas.translate(-32, -32);
            paint.setStrokeWidth(6);
            canvas.drawPath(glyph, paint);
            paint.setStyle(Paint.Style.FILL);
            switch (tone) {
                case WALKING: canvas.drawCircle(37, 9, 6, paint); break;
                case RUNNING: canvas.drawCircle(45, 10, 6, paint); break;
                case STATIONARY: canvas.drawCircle(32, 9, 6, paint); break;
                case INVALID: canvas.drawCircle(32, 48, 3.5f, paint); break;
                default: break;
            }
            canvas.restore();
        }
    }
}
