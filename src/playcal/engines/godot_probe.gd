# playcal's probe, run as `godot --headless --path PROJECT -s godot_probe.gd -- --mode=... --out=FILE`.
# Nothing is copied into the project. Errors go to stderr as Godot prints them; playcal reads them there.
#   --mode=check    load every .gd under res:// so parse errors are printed, then quit
#   --mode=tree     run the main scene for one frame and write its node tree
#   --mode=observe  write the tree after the first frame and again after --seconds
extends SceneTree

var args := {}
var scene: Node
var first = null
var frames := 0
var started := 0


func _initialize() -> void:
	for a in OS.get_cmdline_user_args():
		var kv := a.trim_prefix("--").split("=", true, 1)
		args[kv[0]] = kv[1] if kv.size() > 1 else ""
	if args.get("mode", "observe") == "check":
		var files := []
		_scripts("res://", files)
		for f in files:
			load(f)
		_write({"checked": files.size()})
		quit(0)
		return
	var path: String = args.get("scene", "")
	if path == "":
		path = ProjectSettings.get_setting("application/run/main_scene", "")
	if path == "":
		_write({"error": "the project has no main scene; pass --scene"})
		quit(2)
		return
	var packed = load(path)
	if packed == null:
		_write({"error": "could not load " + path})
		quit(2)
		return
	scene = packed.instantiate()
	root.add_child.call_deferred(scene)
	started = Time.get_ticks_msec()


func _process(_delta: float) -> bool:
	if scene == null or not scene.is_inside_tree():
		return false
	frames += 1
	if first == null:
		first = _node(scene)
		if args.get("mode") == "tree":
			_write({"scene": scene.scene_file_path, "tree": first})
			quit(0)
		return false
	var ms := Time.get_ticks_msec() - started
	if ms >= float(args.get("seconds", "3")) * 1000.0:
		_write({"scene": scene.scene_file_path, "before": first, "after": _node(scene), "played_ms": ms, "frames": frames})
		quit(0)
	return false


func _node(n: Node) -> Dictionary:
	var d := {"name": String(n.name), "type": n.get_class(), "path": String(scene.get_path_to(n))}
	if n.get_script() != null:
		d["script"] = n.get_script().resource_path
	if n is Node2D or n is Node3D:
		d["position"] = var_to_str(n.position)
	if n is CanvasItem or n is Node3D:
		d["visible"] = n.visible
	var kids := []
	for c in n.get_children():
		kids.append(_node(c))
	if kids:
		d["children"] = kids
	return d


func _scripts(dir: String, out: Array) -> void:
	for f in DirAccess.get_files_at(dir):
		if f.ends_with(".gd"):
			out.append(dir.path_join(f))
	for sub in DirAccess.get_directories_at(dir):
		if not sub.begins_with("."):
			_scripts(dir.path_join(sub), out)


func _write(d: Dictionary) -> void:
	var f := FileAccess.open(args.get("out", "user://playcal.json"), FileAccess.WRITE)
	if f:
		f.store_string(JSON.stringify(d))
