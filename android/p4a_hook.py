from pathlib import Path


MARKER = "<!-- lubo-special-use-service -->"
START = "        {% for name, foreground_type in service_data %}"
END = "        {% endfor %}"
REPLACEMENT = """        <!-- lubo-special-use-service -->
        {% for name, foreground_type in service_data %}
        <service android:name="{{ args.package }}.Service{{ name|capitalize }}"
                 {% if foreground_type %}
                 android:foregroundServiceType="{{ foreground_type }}"
                 {% endif %}
                 android:process=":service_{{ name }}">
            {% if name == "recorder" %}
            <property
                android:name="android.app.PROPERTY_SPECIAL_USE_FGS_SUBTYPE"
                android:value="User-started continuous monitoring and local recording of live streams" />
            {% endif %}
        </service>
        {% endfor %}
        <receiver
            android:name="org.lubo.recorder.StopRecorderReceiver"
            android:exported="false" />"""


def patch_manifest_template(path: Path) -> None:
    content = path.read_text(encoding="utf-8")
    if MARKER in content:
        return
    start = content.find(START)
    if start < 0:
        raise RuntimeError("python-for-android service block start was not found")
    end = content.find(END, start)
    if end < 0:
        raise RuntimeError("python-for-android service block end was not found")
    end += len(END)
    path.write_text(content[:start] + REPLACEMENT + content[end:], encoding="utf-8")


def before_apk_build(_toolchain) -> None:
    patch_manifest_template(Path.cwd() / "templates" / "AndroidManifest.tmpl.xml")
