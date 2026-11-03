"""Make the hologram bust's frames (src/lura/modules/overlay/holo/{male,female}.npz).

Needs Blender 4.2+ with the MPFB extension (MakeHuman base mesh, CC0). Two steps:

    blender --background --factory-startup --python tools/holo_bust.py -- OUT 0.0   # female
    blender --background --factory-startup --python tools/holo_bust.py -- OUT2 1.0  # male
    .venv/bin/python tools/holo_bust.py --pack OUT female
    .venv/bin/python tools/holo_bust.py --pack OUT2 male

The first renders 9 turns × 3 mouth openings in Cycles (GPU when there is one) with a
grey hologram material: soft light, Fresnel rim, fine contour lines, only the nearest
surface shown. The second packs the PNGs as luma + alpha; the overlay tints them with
the theme. Add a trailing "quick" to the render step for a single test frame.
"""
import sys

if "--pack" in sys.argv:
    import numpy as np
    import cairo
    from pathlib import Path
    src, name = sys.argv[sys.argv.index("--pack") + 1:][:2]
    tiles = []
    for yi in range(9):
        row = []
        for mi in range(3):
            s = cairo.ImageSurface.create_from_png(f"{src}/y{yi}_m{mi}.png")
            a = np.frombuffer(s.get_data(), np.uint8).reshape(s.get_height(), s.get_stride() // 4, 4)[:, :s.get_width()]
            al = a[..., 3].astype(np.uint16)
            lum = np.where(al > 0, (a[..., 1].astype(np.uint16) * 255 + al // 2) // np.maximum(al, 1), 0)
            row.append(np.stack([lum.clip(0, 255).astype(np.uint8), a[..., 3]], -1))
        tiles.append(row)
    out = Path(__file__).resolve().parents[1] / "src/lura/modules/overlay/holo" / f"{name}.npz"
    np.savez_compressed(out, tiles=np.array(tiles), yaws=np.linspace(-0.3, 0.3, 9),
                        mouths=np.array([0, 0.5, 1.0]), unit=360 / 4.4, centre=np.array([0.0, -0.8]))
    print("wrote", out)
    sys.exit(0)

import bpy, bmesh, addon_utils, importlib, math, os, time
import numpy as np
from mathutils import Vector

argv = sys.argv[sys.argv.index("--") + 1:]
OUT, GENDER = argv[0], float(argv[1])
QUICK = len(argv) > 2
os.makedirs(OUT, exist_ok=True)

for o in list(bpy.data.objects):
    bpy.data.objects.remove(o)
addon_utils.enable('bl_ext.user_default.mpfb', default_set=True)
P = 'bl_ext.user_default.mpfb'
hs = importlib.import_module(P + ".services.humanservice").HumanService
ts = importlib.import_module(P + ".services.targetservice").TargetService
m = ts.get_default_macro_info_dict()
m.update(gender=GENDER, muscle=0.55, age=0.45, weight=0.45)
human = hs.create_human(macro_detail_dict=m)
for mod in human.modifiers:
    mod.show_viewport = mod.show_render = False
dg = bpy.context.evaluated_depsgraph_get()
me = bpy.data.meshes.new_from_object(human.evaluated_get(dg))
co = np.array([v.co[:] for v in me.vertices])
groups = {g.index: g.name for g in human.vertex_groups}
member = {}
for v in human.data.vertices:
    for g in v.groups:
        member.setdefault(groups[g.group], set()).add(v.index)
body, eyes = member['body'], member['helper-l-eye'] | member['helper-r-eye']
keep = body | eyes

def centre(name):
    return co[list(member[name])].mean(0)
mouth, shl = centre('joint-mouth'), centre('joint-l-shoulder')
b = np.array(sorted(body))
cx = b[np.abs(co[b, 0]) < 0.01]
top = co[b, 2].max()
chin = co[cx][(co[cx, 1] < mouth[1] + 0.035) & (co[cx, 2] < mouth[2]) & (co[cx, 2] > mouth[2] - 0.12), 2].min()
eye_z = centre('helper-l-eye')[2]
nose_y = co[cx][(co[cx, 2] > mouth[2]) & (co[cx, 2] < eye_z), 1].min()
s = 2.14 / (top - chin)

# To head units, Blender axes: X right, Y back (front is -Y), Z up; chin -0.98, top 1.16, nose tip 0.84 in front.
U = np.stack([co[:, 0] * s, -((nose_y - co[:, 1]) * s + 0.84), (co[:, 2] - chin) * s - 0.98], 1)
shl_u = np.array([shl[0] * s, 0, (shl[2] - chin) * s - 0.98])

bm = bmesh.new()
bm.from_mesh(me)
bm.verts.ensure_lookup_table()
for v in bm.verts:
    v.co = Vector(U[v.index])
bmesh.ops.delete(bm, geom=[v for v in bm.verts if v.index not in keep], context='VERTS')
bm.normal_update()
inside = []
for f in bm.faces:
    c = f.calc_center_median()
    nf = -f.normal.y                      # towards the viewer
    if abs(c.x) < 0.42 and -0.62 < c.z < 0.45 and -c.y > 0.2 and nf < 0.25:
        inside.append(f)
bmesh.ops.delete(bm, geom=inside, context='FACES')
def cut_off(c, n):
    bmesh.ops.bisect_plane(bm, geom=bm.verts[:] + bm.edges[:] + bm.faces[:], plane_co=c, plane_no=n, clear_outer=True)
cut_off(Vector((0, 0, -2.85)), Vector((0, 0, -1)))
arm = abs(shl_u[0]) * 1.02
for sx in (1, -1):
    cut_off(Vector((sx * arm * 1.06, 0, shl_u[2])), Vector((sx * 1.0, 0, -0.75)).normalized())
mesh = bpy.data.meshes.new("bust")
bm.to_mesh(mesh)
for p in mesh.polygons:
    p.use_smooth = True
obj = bpy.data.objects.new("bust", mesh)
bpy.context.collection.objects.link(obj)
human.hide_render = True
for o in bpy.data.objects:
    if o.type == 'ARMATURE':
        o.hide_render = True
obj.modifiers.new("sub", 'SUBSURF').levels = obj.modifiers["sub"].render_levels = 1
REST = np.array([v.co[:] for v in mesh.vertices])

# Lips meet where the centre profile dips between nose and chin.
c = REST[(np.abs(REST[:, 0]) < 0.03) & (REST[:, 2] > -0.52) & (REST[:, 2] < -0.18) & (REST[:, 1] < -0.5)]
LIP = c[np.argmax(c[:, 1]), 2]          # most recessed (largest Y = furthest back) point
print("LIP", LIP)
def smooth(a, b_, x):
    t = np.clip((x - a) / (b_ - a), 0, 1)
    return t * t * (3 - 2 * t)
JAW = smooth(LIP + 0.02, LIP - 0.08, REST[:, 2]) * smooth(-1.2, -0.95, REST[:, 2]) * smooth(0.25, -0.2, REST[:, 1])

# Material: a hologram in grey — soft light, Fresnel rim, fine contour lines; tinted at run time.
mat = bpy.data.materials.new("holo")
mat.use_nodes = True
nt = mat.node_tree
nt.nodes.clear()
N = nt.nodes.new
L = nt.links.new
out = N("ShaderNodeOutputMaterial")
lw = N("ShaderNodeLayerWeight"); lw.inputs["Blend"].default_value = 0.45
fres = N("ShaderNodeMath"); fres.operation = 'POWER'; fres.inputs[1].default_value = 1.6
L(lw.outputs["Facing"], fres.inputs[0])
tc = N("ShaderNodeTexCoord")
sep = N("ShaderNodeSeparateXYZ"); L(tc.outputs["Object"], sep.inputs[0])
def lines(value_socket, freq, width):
    mul = N("ShaderNodeMath"); mul.operation = 'MULTIPLY'; mul.inputs[1].default_value = freq
    L(value_socket, mul.inputs[0])
    fr = N("ShaderNodeMath"); fr.operation = 'FRACT'; L(mul.outputs[0], fr.inputs[0])
    sub = N("ShaderNodeMath"); sub.operation = 'SUBTRACT'; sub.inputs[1].default_value = 0.5; L(fr.outputs[0], sub.inputs[0])
    ab = N("ShaderNodeMath"); ab.operation = 'ABSOLUTE'; L(sub.outputs[0], ab.inputs[0])
    ss = N("ShaderNodeMapRange"); ss.interpolation_type = 'SMOOTHSTEP'
    ss.inputs["From Min"].default_value = 0.5 - width; ss.inputs["From Max"].default_value = 0.5
    L(ab.outputs[0], ss.inputs["Value"])
    return ss.outputs["Result"]
hl = lines(sep.outputs["Z"], 22.0, 0.07)                      # horizontal contours
ang = N("ShaderNodeMath"); ang.operation = 'ARCTAN2'
L(sep.outputs["X"], ang.inputs[0])
ny = N("ShaderNodeMath"); ny.operation = 'MULTIPLY'; ny.inputs[1].default_value = -1.0; L(sep.outputs["Y"], ny.inputs[0])
L(ny.outputs[0], ang.inputs[1])
vl = lines(ang.outputs[0], 36 / (2 * math.pi), 0.05)           # vertical, every 10°
both = N("ShaderNodeMath"); both.operation = 'MAXIMUM'
L(hl, both.inputs[0])
vls = N("ShaderNodeMath"); vls.operation = 'MULTIPLY'; vls.inputs[1].default_value = 0.6; L(vl, vls.inputs[0])
L(vls.outputs[0], both.inputs[1])
# emission = 0.10 + 1.3·fresnel + 0.45·lines·(0.4 + fresnel)
lf = N("ShaderNodeMath"); lf.operation = 'ADD'; lf.inputs[1].default_value = 0.4; L(fres.outputs[0], lf.inputs[0])
lm = N("ShaderNodeMath"); lm.operation = 'MULTIPLY'; L(both.outputs[0], lm.inputs[0]); L(lf.outputs[0], lm.inputs[1])
lm2 = N("ShaderNodeMath"); lm2.operation = 'MULTIPLY'; lm2.inputs[1].default_value = 1.1; L(lm.outputs[0], lm2.inputs[0])
fr2 = N("ShaderNodeMath"); fr2.operation = 'MULTIPLY_ADD'; fr2.inputs[1].default_value = 1.8; fr2.inputs[2].default_value = 0.06
L(fres.outputs[0], fr2.inputs[0])
em_s = N("ShaderNodeMath"); em_s.operation = 'ADD'; L(fr2.outputs[0], em_s.inputs[0]); L(lm2.outputs[0], em_s.inputs[1])
em = N("ShaderNodeEmission"); em.inputs["Color"].default_value = (1, 1, 1, 1)
L(em_s.outputs[0], em.inputs["Strength"])
dif = N("ShaderNodeBsdfDiffuse"); dif.inputs["Color"].default_value = (0.30, 0.30, 0.30, 1)
add = N("ShaderNodeAddShader"); L(em.outputs[0], add.inputs[0]); L(dif.outputs[0], add.inputs[1])
tr = N("ShaderNodeBsdfTransparent")
mix = N("ShaderNodeMixShader")
# More see-through face-on, solid at the rim, like the reference.
opac = N("ShaderNodeMath"); opac.operation = 'MULTIPLY_ADD'; opac.inputs[1].default_value = 0.5; opac.inputs[2].default_value = 0.45
L(fres.outputs[0], opac.inputs[0])
L(opac.outputs[0], mix.inputs[0]); L(tr.outputs[0], mix.inputs[1]); L(add.outputs[0], mix.inputs[2])
geo = N("ShaderNodeNewGeometry")
lp = N("ShaderNodeLightPath")
# Only the nearest surface shows: back faces and anything seen through the front one
# (the mouth's back wall, the eyeballs) are see-through, or the face gets bright patches.
deeper = N("ShaderNodeMath"); deeper.operation = 'GREATER_THAN'; deeper.inputs[1].default_value = 0.5
L(lp.outputs["Transparent Depth"], deeper.inputs[0])
mix2 = N("ShaderNodeMixShader")
L(deeper.outputs[0], mix2.inputs[0]); L(mix.outputs[0], mix2.inputs[1]); L(tr.outputs[0], mix2.inputs[2])
L(mix2.outputs[0], out.inputs["Surface"])
obj.data.materials.append(mat)

sc = bpy.context.scene
for o in list(bpy.data.objects):
    if o.type in ('LIGHT', 'CAMERA'):
        bpy.data.objects.remove(o)
sun = bpy.data.objects.new("sun", bpy.data.lights.new("sun", 'SUN'))
sun.data.energy = 1.6
sun.rotation_euler = (math.radians(55), 0, math.radians(-35))
sc.collection.objects.link(sun)
cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
cam.data.type = 'ORTHO'
cam.data.ortho_scale = 4.4
cam.location = (0, -30, -0.8)
cam.rotation_euler = (math.radians(90), 0, 0)
sc.collection.objects.link(cam)
sc.camera = cam
w = sc.world or bpy.data.worlds.new("w"); sc.world = w
w.use_nodes = True
w.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.0
sc.render.engine = 'CYCLES'
sc.cycles.samples = 24 if not QUICK else 12
sc.cycles.use_denoising = True
try:
    prefs = bpy.context.preferences.addons['cycles'].preferences
    for kind in ('OPTIX', 'CUDA'):
        try:
            prefs.compute_device_type = kind
            prefs.get_devices()
            for d in prefs.devices:
                d.use = True
            sc.cycles.device = 'GPU'
            print("GPU", kind)
            break
        except Exception as e:
            print("no", kind, e)
except Exception as e:
    print("GPU setup failed", e)
sc.render.film_transparent = True
sc.render.resolution_x = sc.render.resolution_y = 360
sc.render.image_settings.file_format = 'PNG'
sc.render.image_settings.color_mode = 'RGBA'
sc.view_settings.view_transform = 'Standard'

YAWS = np.linspace(-0.3, 0.3, 9) if not QUICK else [0.0]
MOUTHS = (0.0, 0.5, 1.0) if not QUICK else (0.0,)
pivot = np.array([0.0, 0.10, -0.15])     # Blender axes: jaw pivot near the ears
for mi, mo in enumerate(MOUTHS):
    V = REST.copy()
    if mo:
        a = JAW * mo * 0.24
        rel = V - pivot
        c_, s_ = np.cos(a), np.sin(a)
        # open: the chin goes down and slightly back (rotate in the Y–Z plane)
        V[:, 2] = pivot[2] + rel[:, 2] * c_ - (-rel[:, 1]) * s_
        V[:, 1] = pivot[1] - (rel[:, 2] * s_ + (-rel[:, 1]) * c_)
    for yi, yaw in enumerate(YAWS):
        # Same turn as the renderer's _rot: x' = x cos + z_front sin, z_front' = -x sin + z_front cos.
        x, zf = V[:, 0], -V[:, 1]
        W = V.copy()
        W[:, 0] = x * math.cos(yaw) + zf * math.sin(yaw)
        W[:, 1] = -(-x * math.sin(yaw) + zf * math.cos(yaw))
        mesh.vertices.foreach_set("co", W.ravel())
        mesh.update()
        sc.render.filepath = os.path.join(OUT, f"y{yi}_m{mi}.png")
        t0 = time.time()
        bpy.ops.render.render(write_still=True)
        print("RENDERED", sc.render.filepath, round(time.time() - t0, 1), "s", flush=True)
