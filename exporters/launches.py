"""Read the existing launch Article fields for both report formats."""
from i18n import tr
from models.article import Article
from utils.launch_status import launch_status_text


def launch_section_title(launch_range: str) -> str:
    if launch_range == "past_week":
        return tr("launch_section_past")

    if launch_range == "custom":
        return tr("launch_section_custom")
    if launch_range == "past_30_days":
        return tr("launch_section_past30")

    return tr("launch_section_next")


def launch_fields(launch: Article) -> dict[str, str]:
    fields = {
        "name": launch.news.title,
        "time": launch.news.published,
        "rocket": "",
        "provider": "",
        "pad": "",
        "mission_name": "",
        "mission_description": "",
        "orbit": "",
        "status": "",
        "recovery": "",
    }
    labels = {
        "Launch name:": "name",
        "Launch time:": "time",
        "Rocket:": "rocket",
        "Launch service provider:": "provider",
        "Launch pad:": "pad",
        "Mission name:": "mission_name",
        "Mission description:": "mission_description",
        "Orbit:": "orbit",
        "Status:": "status",
        "Recovery:": "recovery",
        "Official or video link:": None,
    }
    multiline_field: str | None = None

    for raw_line in launch.body.splitlines():
        line = raw_line.strip()

        if not line:
            continue

        matched = False

        for label, key in labels.items():
            if not line.startswith(label):
                continue

            matched = True
            multiline_field = key if key == "mission_description" else None

            if key is not None:
                value = line[len(label):].strip()

                if value:
                    fields[key] = value

            break

        if matched:
            continue

        if multiline_field:
            existing = fields[multiline_field]
            fields[multiline_field] = f"{existing}\n{line}".strip()

    fields["status"] = launch_status_text(fields["status"])
    fields["recovery"] = fields["recovery"] or tr("launch_state.unknown")
    return {key: value or tr("not_available") for key, value in fields.items()}
