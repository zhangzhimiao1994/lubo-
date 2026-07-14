package org.lubo.recorder;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.os.Build;
import android.os.Bundle;
import org.kivy.android.PythonActivity;
import org.kivy.android.PythonService;

public class RecorderPythonService extends PythonService {
    @Override
    protected void doStartForeground(Bundle extras) {
        Context context = getApplicationContext();
        int iconId = context.getApplicationInfo().icon;
        String channelId = "lubo_recorder_" + getServiceId();

        NotificationManager manager =
                (NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE);
        NotificationChannel channel = new NotificationChannel(
                channelId,
                "Live room monitoring",
                NotificationManager.IMPORTANCE_LOW);
        channel.setDescription("Active while live rooms are monitored or recorded");
        manager.createNotificationChannel(channel);

        Intent openIntent = new Intent(context, PythonActivity.class);
        PendingIntent openPendingIntent = PendingIntent.getActivity(
                context,
                0,
                openIntent,
                PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);

        Intent stopIntent = new Intent(context, StopRecorderReceiver.class);
        PendingIntent stopPendingIntent = PendingIntent.getBroadcast(
                context,
                getServiceId(),
                stopIntent,
                PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);

        Notification notification = new Notification.Builder(context, channelId)
                .setContentTitle(extras.getString("contentTitle", "Lubo"))
                .setContentText(extras.getString("contentText", "Monitoring live rooms"))
                .setSmallIcon(iconId)
                .setContentIntent(openPendingIntent)
                .setOngoing(true)
                .setOnlyAlertOnce(true)
                .addAction(new Notification.Action.Builder(
                        iconId,
                        "Stop recording",
                        stopPendingIntent).build())
                .build();

        if (Build.VERSION.SDK_INT >= 34) {
            startForeground(
                    getServiceId(),
                    notification,
                    ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE);
        } else {
            startForeground(getServiceId(), notification);
        }
    }
}
