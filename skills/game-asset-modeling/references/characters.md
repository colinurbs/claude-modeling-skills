# Characters: bodies, heads, outfits

Characters are skinned meshes on one shared skeleton. The modeling job is mostly
**conformance**: making a base (usually bought/CC4/ActorCore/MakeHuman-derived) and its
clothing obey the project's skeleton, naming, weights, blendshapes and LOD rules. Read the
spec's `skeleton` block and the delivery doc's character section before touching a mesh.

## Order (blocking dependencies)
1. **Reference skeleton** (`skeleton.ref`): bone names exactly per spec, rest pose per spec
   (irongrip: A-pose, arms ~45° down, palms in, feet hip-width, hips ~0.95 m), `root` on
   the ground as parent of `hips` only, attachment bones. Export as its own glb. Nothing
   else is rigged to anything else.
2. One body + one head + one outfit through the whole pipeline and reviewed in the engine
   (under the project's camera treatment) before any other character work.

## Conforming a bought/CC base
- Import, apply transforms, scale so standing height matches the body variant.
- Retarget/rename bones to the reference (`bone.name = map[old]`), delete extras (twist or
  helper bones not in the list), re-parent to the reference hierarchy; rest pose matches
  the reference (pose-to-rest if needed, then apply the armature modifier to bake).
- Weights: max 4 influences per vertex, normalized (Blender: Limit Total 4 + Normalize All;
  the exporter's `export_influence_nb=4`). Check shoulders, hips and the neck at extreme
  poses.
- Face: blendshapes named exactly as the spec (ARKit names + visemes); required set in
  `skeleton.blendshapes_required_head`.
- Body culling under clothes: vertex colour R = hide on the body mesh (paint per outfit
  coverage or per region).

## Outfits
- Separate skinned meshes on the same skeleton, own glb each (`char_out_<role>_<piece>_a`).
- Build to the body with a small offset (shrinkwrap + solidify), transfer weights from the
  body (Data Transfer, nearest face interpolated), then fix by hand at folds.
- Layer order (shirt < cardigan < coat) with increasing offset; interpenetration tested in
  walk/sit/hands-up poses.
- Texel density per spec class; cloth normal detail from a high-poly sculpt/cloth sim bake.

## Heads and portraits
- Head glb = head mesh + blendshapes + hair (cards with alpha MASK or a shell mesh).
- Portrait render per head per spec (irongrip: 512², neutral front, overcast, Slab
  background) — render in the engine harness or a matched Blender setup.

## Crowd LODs
The crowd runs on the lowest LOD: it must still read as a person with a clean silhouette
at 30 m (keep coat hems, hat brims, bag straps as geometry; drop fingers to mittens, merge
materials to one). Validate with the `lods` and `iso_context` review views.

## Validation for skins (what to check; validator coverage is partial)
- joint names ⊆ spec bone list; count ≤ `max_bones`; only JOINTS_0/WEIGHTS_0 (≤ 4
  influences), weights sum ≈ 1.
- morph target names (mesh `extras.targetNames`) include the required set for heads.
- rest pose bbox: height within tolerance of the variant, feet on y = 0, facing +Z.
Write these as extra checks in the brief's `notes` and verify them with a short script
over the glb (use `validate_glb.load_glb` / `accessor`) until they are folded into the
validator.
