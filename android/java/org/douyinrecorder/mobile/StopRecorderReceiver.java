package org.douyinrecorder.mobile;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;

public final class StopRecorderReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        File request = new File(context.getFilesDir(), "app/stop.request");
        File parent = request.getParentFile();
        if (parent != null) {
            parent.mkdirs();
        }
        try (FileOutputStream output = new FileOutputStream(request, false)) {
            output.write("stop\n".getBytes(StandardCharsets.US_ASCII));
        } catch (IOException ignored) {
            context.stopService(new Intent(context, ServiceRecorder.class));
        }
    }
}
