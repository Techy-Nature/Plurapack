"""Safe, database-independent JSON transfer service.

Converters only produce/consume :class:`TransferSystem`; ownership and SQLite
identifiers are deliberately introduced by :func:`import_system` afterwards.
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable
from urllib.parse import urlparse

from .storage import Member, Store, short_hash

MAX_FILE_SIZE = 5 * 1024 * 1024
MAX_MEMBERS, MAX_GROUPS = 2000, 500
FORMATS = ("plurapack", "pluralkit", "tupperbox")


class TransferError(ValueError):
    """The document is unsafe, invalid, ambiguous, or unsupported."""


@dataclass
class TransferForm:
    export_id: str
    display_name: str
    avatar: str | None = None
    soma: str = ""
    pronouns: str | None = None
    proxy_tags: list[dict[str, str]] = field(default_factory=list)
    banner: str | None = None


@dataclass
class ImportedMember:
    name: str
    prefix: str = ""
    suffix: str = ""
    avatar: str | None = None
    color: str | None = None
    pronouns: str | None = None
    export_id: str = ""
    alias: str | None = None
    description: str = ""
    banner: str | None = None
    proxy_tags: list[dict[str, str]] = field(default_factory=list)
    forms: list[TransferForm] = field(default_factory=list)
    groups: list[str] = field(default_factory=list)
    default_form: str | None = None
    speech_formatting: bool = False
    strikethrough_speech: str = "normal"


@dataclass
class TransferGroup:
    export_id: str
    name: str
    alias: str
    avatar: str | None = None


@dataclass
class ImportedSystem:
    name: str
    members: tuple[ImportedMember, ...]
    description: str = ""
    logo: str | None = None
    tag: str | None = None
    show_tag: bool = True
    banner: str | None = None
    groups: tuple[TransferGroup, ...] = ()
    settings: dict[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class TransferReport:
    format: str
    members_imported: int = 0
    groups_imported: int = 0
    proxy_tags_imported: int = 0
    existing_members_skipped: int = 0
    unsupported_fields_ignored: int = 0
    warnings: tuple[str, ...] = ()


def _obj(v: Any, label: str) -> dict[str, Any]:
    if not isinstance(v, dict):
        raise TransferError(f"{label} must be a JSON object.")
    return v


def _array(v: Any, label: str) -> list[Any]:
    if not isinstance(v, list):
        raise TransferError(f"{label} must be a JSON array.")
    return v


def _text(v: Any, label: str, maximum: int, *, nullable: bool = False) -> str | None:
    if v is None and nullable:
        return None
    if not isinstance(v, str):
        raise TransferError(f"{label} must be text.")
    v = v.strip()
    if len(v) > maximum:
        raise TransferError(f"{label} must be no more than {maximum} characters.")
    return v


def _url(v: Any, label: str) -> str | None:
    value = _text(v, label, 2048, nullable=True)
    if not value:
        return None
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        raise TransferError(f"{label} must be a public HTTP or HTTPS URL.")
    return value


def _tag(v: Any, label: str) -> dict[str, str]:
    item = _obj(v, label)
    prefix = _text(item.get("prefix", ""), f"{label} prefix", 32)
    suffix = _text(item.get("suffix", ""), f"{label} suffix", 32)
    if not prefix and not suffix:
        raise TransferError(f"{label} cannot have both an empty prefix and suffix.")
    return {"prefix": prefix or "", "suffix": suffix or ""}


def parse_json(document: str | bytes) -> dict[str, Any]:
    raw = document.encode() if isinstance(document, str) else document
    if len(raw) > MAX_FILE_SIZE:
        raise TransferError("Import exceeds the 5 MiB upload limit.")
    try:
        return _obj(json.loads(raw), "Import document")
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise TransferError(f"Import document is not valid JSON: {error}.") from error


def detect_format(root: dict[str, Any]) -> str:
    matches: list[str] = []
    if root.get("format") == "plurapack": matches.append("plurapack")
    if isinstance(root.get("tuppers"), list): matches.append("tupperbox")
    # PK's documented full export has a top-level system object plus members/groups.
    if root.get("format") != "plurapack" and isinstance(root.get("members"), list) and (isinstance(root.get("system"), dict) or
                                                   "accounts" in root or "switches" in root):
        matches.append("pluralkit")
    if len(matches) != 1:
        raise TransferError("Import format is ambiguous or unrecognized; specify plurapack, pluralkit, or tupperbox.")
    return matches[0]


def _member(record: dict[str, Any], index: int, *, external: bool = False) -> ImportedMember:
    name = _text(record.get("display_name") or record.get("name"), f"Member {index} name", 80)
    if not name: raise TransferError(f"Member {index} must have a name.")
    tags = [_tag(x, f"Member {index} proxy tag") for x in _array(record.get("proxy_tags", []), "proxy_tags")]
    if not tags:
        tags = [{"prefix": f"{name}:", "suffix": ""}] if external else []
    color = _text(record.get("color"), "Member color", 7, nullable=True)
    if color:
        color = color.removeprefix("#").lower()
        if not re.fullmatch(r"[0-9a-f]{6}", color): raise TransferError("Member color must be six hexadecimal digits.")
        color = "#" + color
    return ImportedMember(name=name, export_id=str(record.get("export_id") or record.get("id") or f"member-{index}"),
        prefix=tags[0]["prefix"] if tags else "", suffix=tags[0]["suffix"] if tags else "",
        proxy_tags=tags, avatar=_url(record.get("avatar_url", record.get("avatar")), "Member avatar"),
        color=color, pronouns=_text(record.get("pronouns"), "Member pronouns", 100, nullable=True),
        alias=_text(record.get("alias"), "Member alias", 24, nullable=True),
        description=_text(record.get("description", ""), "Member description", 1000) or "",
        banner=_url(record.get("banner_url", record.get("banner")), "Member banner"), groups=[str(x) for x in record.get("groups", [])])


def _parse_native(root: dict[str, Any]) -> ImportedSystem:
    if root.get("version") != 1: raise TransferError("Unsupported Plurapack schema version; this installation supports version 1.")
    sys = _obj(root.get("system"), "system")
    members = [_member(_obj(x, "member"), i) for i, x in enumerate(_array(root.get("members"), "members"), 1)]
    groups = tuple(TransferGroup(str(g.get("export_id")), _text(g.get("name"), "Group name", 80) or "",
        _text(g.get("alias"), "Group alias", 24) or "", _url(g.get("avatar_url"), "Group avatar"))
        for g in (_obj(x, "group") for x in _array(root.get("groups", []), "groups")))
    for i, (m, raw) in enumerate(zip(members, root["members"]), 1):
        m.forms = [TransferForm(str(f.get("export_id")), _text(f.get("display_name"), "Form name", 80) or "",
             _url(f.get("avatar_url"), "Form avatar"), _text(f.get("soma", ""), "Form soma", 1000) or "",
             _text(f.get("pronouns"), "Form pronouns", 100, nullable=True),
             [_tag(t, "Form proxy tag") for t in _array(f.get("proxy_tags", []), "form proxy_tags")],
             _url(f.get("banner_url"), "Form banner")) for f in map(lambda x:_obj(x,"form"), raw.get("forms", []))]
        m.default_form = raw.get("default_form")
        settings = _obj(raw.get("settings", {}), "member settings")
        m.speech_formatting = bool(settings.get("speech_formatting", False))
        m.strikethrough_speech = str(settings.get("strikethrough_speech", "normal"))
    return ImportedSystem(_text(sys.get("name"), "System name", 80) or "Imported system", tuple(members),
        _text(sys.get("description", ""), "System description", 1000) or "", _url(sys.get("avatar_url"), "System avatar"),
        _text(sys.get("tag"), "System tag", 32, nullable=True), bool(sys.get("show_tag", True)),
        _url(sys.get("banner_url"), "System banner"), groups, _obj(root.get("settings", {}), "settings"))


def _parse_pk(root: dict[str, Any]) -> ImportedSystem:
    sys = root.get("system") if isinstance(root.get("system"), dict) else root
    members = [_member(_obj(x, "PluralKit member"), i, external=True) for i,x in enumerate(_array(root.get("members"), "PluralKit members"),1)]
    groups=[]
    for i, raw in enumerate(_array(root.get("groups", []), "PluralKit groups"),1):
        g=_obj(raw,"PluralKit group"); gid=str(g.get("id") or f"group-{i}")
        groups.append(TransferGroup(gid,_text(g.get("display_name") or g.get("name"),"Group name",80) or f"Group {i}",f"group{i}"[:24],_url(g.get("icon") or g.get("avatar_url"),"Group avatar")))
        for ref in g.get("members",[]):
            for member in members:
                if member.export_id == str(ref): member.groups.append(gid)
    return ImportedSystem(_text(sys.get("name"),"System name",80) or "Imported PluralKit system",tuple(members),
        _text(sys.get("description", ""),"System description",1000) or "",_url(sys.get("avatar_url"),"System avatar"),
        _text(sys.get("tag"),"System tag",32,nullable=True),groups=tuple(groups),warnings=("PluralKit privacy, switches, accounts, and timestamps were not imported.",))


def _parse_tb(root: dict[str, Any]) -> ImportedSystem:
    groups=[]; group_names={}
    for i,raw in enumerate(_array(root.get("groups",[]),"Tupperbox groups"),1):
        g=_obj(raw,"Tupperbox group"); gid=str(g.get("id") or f"group-{i}"); name=_text(g.get("name"),"Group name",80) or f"Group {i}"
        groups.append(TransferGroup(gid,name,re.sub(r"\s+","-",name)[:24] or f"group{i}",_url(g.get("avatar"),"Group avatar"))); group_names[name.casefold()]=gid
    members=[]
    for i,raw in enumerate(_array(root.get("tuppers"),"Tupperbox tuppers"),1):
        r=_obj(raw,"Tupperbox tupper"); brackets=_array(r.get("brackets",[]),"Tupperbox brackets")
        if len(brackets)%2: raise TransferError("Tupperbox brackets must contain flat prefix/suffix pairs.")
        r=dict(r); r["proxy_tags"]=[{"prefix":brackets[n],"suffix":brackets[n+1]} for n in range(0,len(brackets),2)]
        m=_member(r,i,external=True); ref=r.get("group_id")
        if ref is not None: m.groups=[str(ref)]
        elif r.get("group_name") and str(r["group_name"]).casefold() in group_names: m.groups=[group_names[str(r["group_name"]).casefold()]]
        members.append(m)
    return ImportedSystem(_text(root.get("name"),"System name",80,nullable=True) or "Imported Tupperbox system",tuple(members),groups=tuple(groups),warnings=("Tupperbox IDs, ownership, usage counts, timestamps, birthdays, and bracket-display flags were not imported.",))


def parse_import(source: str | None, document: str | bytes) -> ImportedSystem:
    root=parse_json(document); fmt=source.casefold().replace("-","") if source else detect_format(root)
    aliases={"pk":"pluralkit","tupper":"tupperbox"}; fmt=aliases.get(fmt,fmt)
    if fmt not in FORMATS: raise TransferError("Source must be pluralkit, tupperbox, or plurapack.")
    result={"plurapack":_parse_native,"pluralkit":_parse_pk,"tupperbox":_parse_tb}[fmt](root)
    if len(result.members)>MAX_MEMBERS or len(result.groups)>MAX_GROUPS: raise TransferError("Import exceeds member or group count limits.")
    mids=[m.export_id for m in result.members]; gids={g.export_id for g in result.groups}
    if len(set(x.casefold() for x in (m.name for m in result.members))) != len(result.members): raise TransferError("Imported member names must be unique.")
    if len(set(mids)) != len(mids): raise TransferError("Imported member references must be unique.")
    for m in result.members:
        if any(g not in gids for g in m.groups): raise TransferError(f"Member {m.name!r} references an unknown group.")
    return result


def _snapshot(store: Store, account_id: str) -> ImportedSystem:
    sid=store.system_for(account_id)
    if not sid: raise PermissionError("Create a system first.")
    system=store.system_info(sid); assert system
    with store.connect() as db:
        group_rows=db.execute("SELECT * FROM groups WHERE system_id=? ORDER BY name",(sid,)).fetchall()
        memberships={r["member_id"]:[] for r in db.execute("SELECT id AS member_id FROM members WHERE system_id=?",(sid,))}
        for r in db.execute("SELECT gm.member_id,gm.group_id FROM group_members gm JOIN groups g ON g.id=gm.group_id WHERE g.system_id=?",(sid,)): memberships[r["member_id"]].append("group-"+r["group_id"])
    groups=tuple(TransferGroup("group-"+g["id"],g["name"],g["alias"],g["avatar"]) for g in group_rows)
    members=[]
    for member in store.members_for_system(sid):
        forms=[]
        for f in store.forms_for_member(member.id):
            forms.append(TransferForm("form-"+f.id,f.display_name,f.avatar,f.soma,f.pronouns,[asdict(t) for t in store.proxy_tags(form_id=f.id)],f.banner))
        members.append(ImportedMember(member.name,member.prefix,member.suffix,member.avatar,member.color,member.pronouns,
            "member-"+member.id,member.alias,member.description,member.banner,[asdict(t) for t in store.proxy_tags(member_id=member.id)],forms,memberships.get(member.id,[]),
            "form-"+member.default_form_id if member.default_form_id else None,bool(member.speech_formatting),member.strikethrough_speech))
    return ImportedSystem(system.display_name,tuple(members),system.description,system.logo,system.system_tag,bool(system.show_system_tag),system.banner,groups)


def _external_members(data: ImportedSystem, forms_mode: str) -> tuple[list[ImportedMember], int]:
    """Return members for a compatibility export, optionally promoting forms.

    External formats have no Plurapack form concept.  Promotion uses a visibly
    qualified name to avoid collisions while retaining the form's presentation
    and its parent's color and group memberships.
    """
    if forms_mode not in {"loss", "members"}:
        raise TransferError("Forms mode must be loss or members.")
    members = list(data.members)
    forms = sum((member.forms for member in data.members), [])
    if forms_mode == "loss":
        return members, len(forms)
    for parent in data.members:
        for form in parent.forms:
            members.append(ImportedMember(
                name=f"{parent.name} — {form.display_name}",
                prefix=form.proxy_tags[0]["prefix"] if form.proxy_tags else "",
                suffix=form.proxy_tags[0]["suffix"] if form.proxy_tags else "",
                avatar=form.avatar if form.avatar is not None else parent.avatar,
                color=parent.color,
                pronouns=form.pronouns if form.pronouns is not None else parent.pronouns,
                export_id=form.export_id,
                description=form.soma or parent.description,
                banner=form.banner or parent.banner,
                proxy_tags=list(form.proxy_tags),
                groups=list(parent.groups),
            ))
    names: dict[str, str] = {}
    for member in members:
        key = member.name.casefold()
        if key in names:
            raise TransferError(
                f"Promoted form name {member.name!r} conflicts with exported member {names[key]!r}. "
                "Rename the member or form before using forms_mode='members'."
            )
        names[key] = member.name
    return members, len(forms)


def _forms_warning(forms_mode: str, form_count: int, noun: str) -> str:
    if not form_count:
        return ""
    if forms_mode == "loss":
        return f"{form_count} forms were omitted; use forms_mode='members' to export them as {noun}. "
    return f"{form_count} forms were exported as qualified standalone {noun}. "


def export_system(store: Store, account_id: str, format_name: str="plurapack",
                  group_id: str | None=None, forms_mode: str = "loss") -> tuple[bytes,TransferReport]:
    data=_snapshot(store,account_id); fmt=format_name.casefold()
    if group_id:
        ref="group-"+group_id; data.members=tuple(m for m in data.members if ref in m.groups); data.groups=tuple(g for g in data.groups if g.export_id==ref)
    now=datetime.now(timezone.utc).isoformat().replace("+00:00","Z")
    if fmt=="plurapack":
        body={"format":"plurapack","version":1,"exported_at":now,"generator":{"name":"Plurapack","version":"0.1.0"},
          "system":{"name":data.name,"description":data.description,"tag":data.tag,"avatar_url":data.logo,"banner_url":data.banner,"show_tag":data.show_tag},
          "members":[{"export_id":m.export_id,"name":m.name,"alias":m.alias,"description":m.description,"pronouns":m.pronouns,"color":m.color,"avatar_url":m.avatar,"banner_url":m.banner,"proxy_tags":m.proxy_tags,"groups":m.groups,"forms":[asdict(f) | {"avatar_url":f.avatar,"banner_url":f.banner} for f in m.forms],"default_form":m.default_form,"settings":{"speech_formatting":m.speech_formatting,"strikethrough_speech":m.strikethrough_speech}} for m in data.members],"groups":[{"export_id":g.export_id,"name":g.name,"alias":g.alias,"avatar_url":g.avatar} for g in data.groups],"settings":{}}
        warnings=()
    elif fmt=="pluralkit":
        external, form_count = _external_members(data, forms_mode)
        body={"version":2,"system":{"name":data.name,"description":data.description,"tag":data.tag,"avatar_url":data.logo},"members":[{"id":m.export_id,"name":m.name,"display_name":None,"description":m.description,"pronouns":m.pronouns,"color":m.color.removeprefix("#") if m.color else None,"avatar_url":m.avatar,"banner":m.banner,"proxy_tags":m.proxy_tags,"privacy":None} for m in external],"groups":[{"id":g.export_id,"name":g.name,"display_name":None,"description":None,"icon":g.avatar,"members":[m.export_id for m in external if g.export_id in m.groups]} for g in data.groups],"switches":[]}
        warnings=(_forms_warning(forms_mode, form_count, "members") + "Aliases, speech settings, and system tag visibility are not representable in PluralKit export schema v2.",)
    elif fmt=="tupperbox":
        external, form_count = _external_members(data, forms_mode)
        body={"tuppers":[{"id":m.export_id,"name":m.name,"nick":None,"description":m.description,"avatar_url":m.avatar,"banner":m.banner,"brackets":[x for t in m.proxy_tags for x in (t["prefix"],t["suffix"])],"group_id":m.groups[0] if m.groups else None} for m in external],"groups":[{"id":g.export_id,"name":g.name,"avatar":g.avatar} for g in data.groups]}
        warnings=(_forms_warning(forms_mode, form_count, "tuppers") + "Colors, pronouns, aliases, additional group memberships, and Plurapack settings are not representable in Tupperbox exports.",)
    else: raise TransferError("Format must be pluralkit, tupperbox, or plurapack.")
    return (json.dumps(body,ensure_ascii=False,indent=2)+"\n").encode(),TransferReport(fmt,warnings=warnings)


def export_document(format_name: str, system_name: str, members: Iterable[Member]) -> bytes:
    """Legacy converter retained for integrations; new callers should use export_system."""
    values=list(members)
    if format_name=="plurapack": return (json.dumps({"format":"plurapack","version":1,"name":system_name,"system":{"name":system_name},"members":[{"export_id":f"member-{i}","name":m.name,"proxy_tags":[{"prefix":m.prefix,"suffix":m.suffix}]} for i,m in enumerate(values)],"groups":[],"settings":{}},indent=2)+"\n").encode()
    if format_name=="pluralkit": body={"name":system_name,"system":{"name":system_name},"members":[{"name":m.name,"avatar_url":m.avatar,"color":m.color,"proxy_tags":[{"prefix":m.prefix,"suffix":m.suffix}]} for m in values],"groups":[],"switches":[]}
    elif format_name=="tupperbox": body={"name":system_name,"tuppers":[{"name":m.name,"brackets":[m.prefix,m.suffix],"avatar_url":m.avatar} for m in values],"groups":[]}
    else: raise TransferError("Format must be pluralkit, tupperbox, or plurapack.")
    return (json.dumps(body,ensure_ascii=False,indent=2)+"\n").encode()


def import_system(store: Store, account_id: str, document: str | bytes, format_name: str | None=None, strategy: str="merge") -> TransferReport:
    if strategy not in {"merge","skip-existing","overwrite"}: raise TransferError("Strategy must be merge, skip-existing, or overwrite.")
    data=parse_import(format_name,document); fmt=format_name or detect_format(parse_json(document)); imported=skipped=groups_count=tags_count=0
    with store.connect() as db:
        owner=db.execute("SELECT system_id FROM owners WHERE account_id=?",(account_id,)).fetchone()
        if owner: sid=owner[0]
        else:
            sid=short_hash(10); db.execute("INSERT INTO systems(id,display_name,description,logo,system_tag,show_system_tag,banner) VALUES (?,?,?,?,?,?,?)",(sid,data.name,data.description,data.logo,data.tag,int(data.show_tag),data.banner)); db.execute("INSERT INTO owners VALUES (?,?)",(account_id,sid))
        existing={r["name"].casefold():r for r in db.execute("SELECT * FROM members WHERE system_id=?",(sid,))}
        group_map={}; existing_groups={r["name"].casefold():r for r in db.execute("SELECT * FROM groups WHERE system_id=?",(sid,))}
        for i,g in enumerate(data.groups):
            row=existing_groups.get(g.name.casefold())
            if row:
                group_map[g.export_id]=row["id"]
                if strategy == "overwrite":
                    db.execute("UPDATE groups SET alias=?,avatar=? WHERE id=?",
                               (g.alias, g.avatar, row["id"]))
                elif strategy == "merge" and not row["avatar"] and g.avatar:
                    db.execute("UPDATE groups SET avatar=? WHERE id=?", (g.avatar, row["id"]))
            else:
                gid=short_hash(5); alias=g.alias or f"group{i+1}"; db.execute("INSERT INTO groups(id,system_id,name,alias,avatar) VALUES (?,?,?,?,?)",(gid,sid,g.name,alias,g.avatar)); group_map[g.export_id]=gid; groups_count+=1
        member_map={}
        actions={}
        for m in data.members:
            row=existing.get(m.name.casefold())
            if row and strategy=="skip-existing":
                skipped+=1; member_map[m.export_id]=row["id"]; actions[m.export_id]="skip"; continue
            if row and strategy=="merge":
                mid=row["id"]
                # Empty/null presentation fields inherit useful imported values;
                # populated local values and boolean preferences always win.
                merged={key: row[key] if row[key] not in {None, ""} else value for key,value in {
                    "alias":m.alias,"description":m.description,"pronouns":m.pronouns,
                    "color":m.color,"avatar":m.avatar,"banner":m.banner}.items()}
                db.execute("UPDATE members SET alias=?,description=?,pronouns=?,color=?,avatar=?,banner=? WHERE id=?",
                           (merged["alias"],merged["description"],merged["pronouns"],merged["color"],
                            merged["avatar"],merged["banner"],mid))
                current_tags={(tag["prefix"],tag["suffix"]) for tag in db.execute(
                    "SELECT prefix,suffix FROM proxy_tags WHERE member_id=?",(mid,))}
                for t in m.proxy_tags:
                    pair=(t["prefix"],t["suffix"])
                    if pair not in current_tags:
                        db.execute("INSERT INTO proxy_tags(system_id,member_id,prefix,suffix) VALUES (?,?,?,?)",
                                   (sid,mid,*pair)); current_tags.add(pair); tags_count+=1
                member_map[m.export_id]=mid; actions[m.export_id]="merge"; continue
            if row:
                mid=row["id"]
                db.execute("UPDATE members SET name=?,prefix=?,suffix=?,alias=?,description=?,pronouns=?,color=?,avatar=?,banner=?,speech_formatting=?,strikethrough_speech=?,default_form_id=NULL WHERE id=?",(m.name,m.prefix,m.suffix,m.alias,m.description,m.pronouns,m.color,m.avatar,m.banner,int(m.speech_formatting),m.strikethrough_speech,mid))
                db.execute("DELETE FROM proxy_tags WHERE member_id=?",(mid,))
            else:
                mid=short_hash(5); db.execute("INSERT INTO members(id,system_id,name,prefix,suffix,avatar,color,alias,description,pronouns,banner,speech_formatting,strikethrough_speech) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",(mid,sid,m.name,m.prefix,m.suffix,m.avatar,m.color,m.alias,m.description,m.pronouns,m.banner,int(m.speech_formatting),m.strikethrough_speech)); imported+=1
            member_map[m.export_id]=mid
            actions[m.export_id]="overwrite" if row else "create"
            for t in m.proxy_tags: db.execute("INSERT INTO proxy_tags(system_id,member_id,prefix,suffix) VALUES (?,?,?,?)",(sid,mid,t["prefix"],t["suffix"])); tags_count+=1
        for m in data.members:
            mid=member_map[m.export_id]
            action=actions[m.export_id]
            if action == "skip":
                continue
            if action == "overwrite":
                db.execute("DELETE FROM group_members WHERE member_id=?",(mid,))
            for ref in m.groups: db.execute("INSERT OR IGNORE INTO group_members(group_id,member_id) VALUES (?,?)",(group_map[ref],mid))
            form_map={}
            if action == "overwrite":
                db.execute("DELETE FROM forms WHERE member_id=?",(mid,))
            if action in {"create", "overwrite"}:
                for f in m.forms:
                    fid=short_hash(5); db.execute("INSERT INTO forms(id,member_id,display_name,avatar,soma,pronouns,prefix,suffix,banner) VALUES (?,?,?,?,?,?,?,?,?)",(fid,mid,f.display_name,f.avatar,f.soma,f.pronouns,f.proxy_tags[0]["prefix"] if f.proxy_tags else "",f.proxy_tags[0]["suffix"] if f.proxy_tags else "",f.banner)); form_map[f.export_id]=fid
                    for t in f.proxy_tags: db.execute("INSERT INTO proxy_tags(system_id,form_id,prefix,suffix) VALUES (?,?,?,?)",(sid,fid,t["prefix"],t["suffix"])); tags_count+=1
            elif action == "merge":
                existing_forms={r["display_name"].casefold():r for r in db.execute(
                    "SELECT * FROM forms WHERE member_id=?",(mid,))}
                for f in m.forms:
                    form_row=existing_forms.get(f.display_name.casefold())
                    if form_row:
                        fid=form_row["id"]
                        values={key:form_row[key] if form_row[key] not in {None,""} else value for key,value in {
                            "avatar":f.avatar,"soma":f.soma,"pronouns":f.pronouns,"banner":f.banner}.items()}
                        db.execute("UPDATE forms SET avatar=?,soma=?,pronouns=?,banner=? WHERE id=?",
                                   (values["avatar"],values["soma"],values["pronouns"],values["banner"],fid))
                        current_tags={(tag["prefix"],tag["suffix"]) for tag in db.execute(
                            "SELECT prefix,suffix FROM proxy_tags WHERE form_id=?",(fid,))}
                        for t in f.proxy_tags:
                            pair=(t["prefix"],t["suffix"])
                            if pair not in current_tags:
                                db.execute("INSERT INTO proxy_tags(system_id,form_id,prefix,suffix) VALUES (?,?,?,?)",
                                           (sid,fid,*pair)); current_tags.add(pair); tags_count+=1
                    else:
                        fid=short_hash(5)
                        db.execute("INSERT INTO forms(id,member_id,display_name,avatar,soma,pronouns,prefix,suffix,banner) VALUES (?,?,?,?,?,?,?,?,?)",(fid,mid,f.display_name,f.avatar,f.soma,f.pronouns,f.proxy_tags[0]["prefix"] if f.proxy_tags else "",f.proxy_tags[0]["suffix"] if f.proxy_tags else "",f.banner))
                        for t in f.proxy_tags:
                            db.execute("INSERT INTO proxy_tags(system_id,form_id,prefix,suffix) VALUES (?,?,?,?)",
                                       (sid,fid,t["prefix"],t["suffix"])); tags_count+=1
                    form_map[f.export_id]=fid
            if m.default_form in form_map and (action != "merge" or not db.execute(
                    "SELECT default_form_id FROM members WHERE id=?",(mid,)).fetchone()[0]):
                db.execute("UPDATE members SET default_form_id=? WHERE id=?",(form_map[m.default_form],mid))
    return TransferReport(str(fmt),imported,groups_count,tags_count,skipped,len(data.warnings),data.warnings)
