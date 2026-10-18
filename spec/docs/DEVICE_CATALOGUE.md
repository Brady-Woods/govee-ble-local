# Govee device catalogue — goodsType universe vs the spec

The Govee Home 7.7.02 app recognises **390 goodsType** values (its device-type key; a SKU maps to a goodsType, sometimes refined by pactType/pactCode). The spec's `devices.yaml` models **79** of them across **51** families. This catalogue lists the whole universe, what the spec covers, and what it does not, from a source-grounded cross-check of the app's `GoodsType` / `Pact` registry (every claim cited in the raw cross-check JSON). Classifications were adversarially verified.

**Legend.** *modeled* = in a `devices.yaml` family. *lighting/plug (unmodeled)* = a BLE-controlled light or plug the spec does not model yet — many live in pact splits not present in this decompile. *out-of-scope* = appliance / gateway / sensor the library does not target. *alias* = a SKU already in a modeled family under another goodsType.

## Summary

- app goodsTypes: 390
- missing_lighting_family: 147
- out_of_scope: 106
- modeled: 79
- alias: 38
- unknown: 20

## Modeled families (`devices.yaml`)

| Family | Category | goodsTypes | SKUs (sample) |
|---|---|---|---|
| h60ax | rgbic_strip | 188, 235, 302, 303, 324, 346, 356, 370, 373, 390 | H60A0, H60A1, H60A4, H60A6, H60C1, H1270, H1232, H1250 |
| h6047 | light_bar | 119, 196, 197, 277, 288, 298 | H6047, H6043, H6042, H6039, H6038, H6048, H8048 |
| bulblightv3 | bulb | 0, 9, 10, 20, 31, 64, 111, 311, 328, 342, 348 | H6001, H6002, H6003, H6004, H6005, H6006, H6008, H6009 |
| h61d3 | rgbic_strip | 202, 239, 242, 246, 247, 261, 263, 264, 299, 367, 375, 384, 387, 388, 389, 400 | H61B0, H61B3, H61B6, H61B8, H61B9, H61D2, H61D3, H61D4 |
| striplight | panel | 41 | H6062 |
| h5080 | plug | 43, 50, 90, 307 | H5080, H5082, H5083, H5085, H5089, H5160, H5161 |
| h6159 | rgbic_strip | 40 *(filled from app; was empty)* | H6159, H6110, H6109, H614A, H614B, H614E |
| h6160 | rgbic_strip | 19, 40 *(filled from app; was empty)* | H6160, H6163, H6117 |
| h6188 | rgb_strip | 0 | H6188 |
| h6061 | panel | 51 | H6061 |
| h6102 | rgbic_strip | 18 *(filled from app; was empty)* | H6102 |
| h6104 | rgbic_strip | — *(no app goodsType; SKU-only)* | H6104 |
| h6105 | rgbic_strip | — *(no app goodsType; SKU-only)* | H6105 |
| h6113 | rgbic_strip | 39 *(filled from app; was empty)* | H6113 |
| h6114 | rgbic_strip | 39 *(filled from app; was empty)* | H6114 |
| h6119 | rgbic_strip | 44 *(filled from app; was empty)* | H6119 |
| h612526 | rgbic_strip | 17 *(filled from app; was empty)* | H6125, H6126 |
| h6127 | rgbic_strip | — *(no app goodsType; SKU-only)* | H6107, H6116, H6127, H6161 |
| h6129 | rgbic_strip | — *(no app goodsType; SKU-only)* | H6129 |
| h613839 | rgbic_strip | 39 *(filled from app; was empty)* | H6138, H6139, H613A, H613B, H613C, H613D, H613E, H613F |
| h6181 | rgb_strip | — *(no app goodsType; SKU-only)* | H6181 |
| h6182 | rgb_strip | — *(no app goodsType; SKU-only)* | H6182 |
| h6185 | rgb_strip | — *(no app goodsType; SKU-only)* | H6185 |
| h6101 | rgb_strip | — *(no app goodsType; SKU-only)* | H6101 |
| dreamcolorlightv1 | rgbic_strip | 17, 18, 70, 73, 204, 207 | H6102, H611B, H611Z, H6123, H612A, H612B, H612C, H612D |
| dreamcolorlightv2 | rgbic_strip | 21 *(filled from app; was empty)* | H6102, H6116, H6117, H611A, H6125, H6126, H6127, H6143 |
| rgblight | rgb_strip | 1, 2, 6, 12, 16, 39, 40, 68, 78, 108, 143 *(filled from app; was empty)* | — |
| rgbiclight | rgbic_strip | 11, 13, 17, 18, 19, 21, 44, 70, 71, 73, 74, 75, 83, 123, 127, 136, 141, 162, 175, 176, 178, 187, 193, 204, 207, 234, 262, 332, 351, 352 *(filled from app; was empty)* | — |
| stringlightv2 | string_light | 2, 39, 68, 78, 143 | H6109, H6110, H6118, H6121, H6138, H6141, H6142, H6154 |
| bulblightstringv1 | string_light | 3 | H7002, H7005 |
| barelightv1 | neon | 13 *(filled from app; was empty)* | H6145, H6146, H6147, H6171 |
| tablelampv1 | lamp | 22, 128 | H6052, H6078 |
| homelightv1 | lamp | 16 *(filled from app; was empty)* | H6148 |
| hollowlamp | panel | 27, 37, 42, 58, 59, 84, 395 | H6050, H6051, H6055, H6058, H6059, H6073 |
| carlightv1 | car_light | 6 *(filled from app; was empty)* | H6118, H6194 |
| h604a | rgbic_strip | 95, 109, 122, 133 *(filled from app; was empty)* | H604A, H604B, H604C, H604D |
| h6057 | lamp | 104 | H6057 |
| h1162 | hdmi_sync | — *(no app goodsType; SKU-only)* | H1162, H1163, H1167, H1168 |
| h70b1 | splicing_panel | 134 *(filled from app; was empty)* | H70B1 |
| h70b2 | splicing_panel | 185 *(filled from app; was empty)* | H70B2 |
| h70bx | splicing_panel | 134, 185, 216 *(filled from app; was empty)* | H70B1, H70B2, H70BC |
| h705a | rgbic_strip | 125, 141, 180, 240, 250 *(filled from app; was empty)* | H705a, H705b, H705D, H705E, H705F, H3401, H61C2, H61C3 |
| h7004 | grow_light | — *(no app goodsType; SKU-only)* | H7004 |
| h7017 | grow_light | — *(no app goodsType; SKU-only)* | H7017 |
| h7022 | string_light | — *(no app goodsType; SKU-only)* | H7022 |
| tvlightv1 | tv_backlight | 12, 39 *(filled from app; was empty)* | H6179 |
| pact_tvlightv2 | tv_backlight | 24 *(filled from app; was empty)* | H6198, H6199 |
| pact_tvlightv3 | tv_backlight | 23 *(filled from app; was empty)* | H6046, H6053, H6056 |
| pact_tvlightv4 | tv_backlight | 25 *(filled from app; was empty)* | H6049, H6054 |
| pact_h605b | hdmi_sync | 172 *(filled from app; was empty)* | H605B, H605C, H605D, H6601, H6602, H6603, H6604, H6608 |
| h512x | sensor | 130, 131, 132, 139, 144, 145, 206, 238 | H5121, H5122, H5123, H5124, H5125, H5126, H5129, H5130 |

## Unmodeled lighting / plug goodsTypes (candidates for future support)

BLE-controlled lights or plugs the app knows but the spec does not model. `decompiled=no` means the controller code is in a pact split not in this decompile, so its opcodes were not traced.

| goodsType | SKUs | category | decompiled | BLE-control |
|---|---|---|---|---|
| 30 | H6072, H8072 | lamp | yes | yes |
| 35 | unknown | string_light | yes | yes |
| 36 | ? | outdoor_light | no | unknown |
| 45 | ? | outdoor_light | no | unknown |
| 46 | ? | unknown | no | unknown |
| 49 | unknown | string_light | yes | yes |
| 53 | unknown | unknown | no | unknown |
| 54 | unknown | unknown | no | unknown |
| 55 | unknown | outdoor_light | no | unknown |
| 56 | unknown | outdoor_light | no | unknown |
| 57 | unknown | lamp | no | unknown |
| 62 | unknown | unknown | no | yes |
| 63 | unknown | unknown | no | yes |
| 67 | H6075 (name-level only; shared with goodsType 57) | lamp | no | yes |
| 69 | H6076, H8076 | lamp | yes | yes |
| 76 | unknown | unknown | no | unknown |
| 77 | unknown | unknown | no | unknown |
| 80 | H610A | panel | yes | yes |
| 82 | unknown | tv_backlight | no | unknown |
| 91 | unknown | string_light | yes | yes |
| 96 | H6066 | panel | yes | yes |
| 97 | H6067 | panel | yes | yes |
| 98 | unknown | unknown | no | yes |
| 100 | unknown | unknown | no | yes |
| 101 | unknown | unknown | no | unknown |
| 102 | unknown | unknown | no | unknown |
| 105 | unknown | unknown | no | unknown |
| 107 | unknown | unknown | no | unknown |
| 113 | unknown | unknown | no | yes |
| 114 | H610B | panel | yes | yes |
| 116 | unknown | unknown | no | yes |
| 121 | H6065 | panel | yes | yes |
| 127 | H7095 | car_light | yes | yes |
| 138 | unknown | tv_backlight | no | unknown |
| 148 | H7019 | string_light | yes | yes |
| 149 | unknown | unknown | no | unknown |
| 156 | H6079 | lamp | no | unknown |
| 157 | H6088 | unknown | no | yes |
| 161 | H7033 | unknown | no | unknown |
| 162 | H7092 | car_light | yes | yes |
| 163 | H606A | panel | yes | yes |
| 167 | unknown | other_light | no | unknown |
| 171 | ? | unknown | no | yes |
| 173 | H6609 | rgbic_strip | no | yes |
| 175 | H61BE | rgbic_strip | yes | yes |
| 176 | H61BC, H6176 | rgbic_strip | yes | yes |
| 178 | H61BA | rgbic_strip | yes | yes |
| 181 | unknown | rgbic_light (form factor unknown) | no | unknown |
| 184 | H608A, H608B, H608C, H608D, H808A | downlight ("String Downlights") | no | yes |
| 187 | H6175 | rgbic_strip | yes | yes |
| 195 | H5086 | plug | yes | yes |
| 203 | H6098, H8098 | tv_backlight | no | unknown |
| 205 | H7075 | light (subtype unknown; segmented DIY-effect light) | no | unknown |
| 209 | H607C | floor_lamp | no | yes |
| 210 | H6020 | speaker_light | no | yes |
| 218 | ? | rgbic_light | no | yes |
| 219 | ? | rgbic_light | no | yes |
| 221 | H7037 | rgbic_light | no | yes |
| 227 | unknown | light (form factor unknown) | no | yes |
| 228 | H6063 | panel | yes | yes |
| 229 | unknown | splicing_panel | no | yes |
| 230 | unknown | splicing_panel | no | yes |
| 231 | unknown | splicing_panel | no | yes |
| 245 | H7078 | outdoor_light | no | yes |
| 249 | H6022 | lamp | no | yes |
| 253 | unknown | tv_backlight | no | yes |
| 255 | unknown | splicing_panel | no | yes |
| 256 | unknown | splicing_panel | no | yes |
| 260 | H6871, R6871, H70C9 | unknown | no | yes |
| 262 | H6169 | rgbic_strip | yes | yes |
| 267 | unknown | unknown | no | yes |
| 268 | unknown | unknown | no | yes |
| 269 | unknown | unknown | no | yes |
| 271 | H6093 | projector | no | yes |
| 272 | unknown | pixel_matrix | no | yes |
| 273 | unknown | tv_backlight | no | yes |
| 274 | unknown | unknown | no | yes |
| 275 | unknown | unknown | no | yes |
| 278 | unknown | pixel_matrix | no | yes |
| 279 | unknown | unknown | no | yes |
| 280 | H6820, H6821 | unknown | no | yes |
| 283 | unknown | unknown | no | unknown |
| 284 | H6069 | panel | yes | yes |
| 285 | unknown | pixel_display | no | unknown |
| 286 | unknown | pixel_display | no | unknown |
| 289 | unknown | unknown | no | unknown |
| 290 | unknown | light | no | yes |
| 293 | unknown | outdoor_light | no | yes |
| 294 | H7072 | light | no | yes |
| 296 | unknown | light | no | yes |
| 300 | unknown | lamp | no | yes |
| 301 | H60B0 | lamp | no | yes |
| 304 | unknown | outdoor_light | no | yes |
| 305 | unknown | unknown | no | yes |
| 312 | H60B2 | lamp | yes | yes |
| 313 | unknown | unknown | no | unknown |
| 315 | unknown | unknown | no | unknown |
| 316 | unknown | unknown | no | unknown |
| 318 | unknown | unknown | no | unknown |
| 322 | unknown | projector_light | no | yes |
| 323 | unknown | projector_light | no | yes |
| 325 | unknown | pixel_matrix_light | no | yes |
| 326 | unknown | rgbic_light | no | yes |
| 327 | unknown | tv_backlight | no | yes |
| 329 | unknown | pixel_matrix_light | no | yes |
| 331 | unknown | projector | no | unknown |
| 332 | H6671, H6672 | rgbic_strip | yes | yes |
| 333 | unknown | projector | no | unknown |
| 334 | unknown | projector | no | unknown |
| 336 | unknown | tv_backlight | no | unknown |
| 337 | unknown | unknown | no | unknown |
| 338 | H7045, H7046 | rgbic_light | no | unknown |
| 339 | unknown | rgbic_light | no | unknown |
| 340 | H6095 (name-level only: SleepActionUiH6095 is the sleep-action UI used only by 340) | star_projector | no | unknown |
| 341 | unknown | pixel_matrix_panel | no | unknown |
| 345 | unknown | rgbic_light | no | unknown |
| 349 | unknown | rgbic_light | no | unknown |
| 353 | unknown | light (form factor unknown) | no | unknown |
| 354 | unknown | light (form factor unknown) | no | unknown |
| 359 | unknown | fan_light (ceiling fan + light) | no | unknown |
| 360 | unknown | tv_backlight (inferred: camera device) | no | unknown |
| 361 | unknown | light (form factor unknown) | no | unknown |
| 362 | unknown | light (form factor unknown) | no | unknown |
| 364 | H1630 (paired by name only: the goodsType==364 branch calls ScenesOp.parseScenes4H1630Graffiti, and goodsType 364 maps to the color_tem_beta_notice_h1630 string; no SKU string literal is paired with 364) | rgbic_light | no | yes |
| 365 | H1741 (paired by name only, weak: the goodsType==365 branch selects the R.string.h1741_day_sync_iot_off string; no SKU string literal is paired with 365) | rgbic_light | no | yes |
| 368 | unknown | fan_light | no | yes |
| 371 | unknown | light | no | yes |
| 374 | unknown | unknown | no | yes |
| 376 | H16B0 | lamp | no | yes |
| 377 | H16C0 | lamp | yes | yes |
| 378 | unknown | unknown | no | yes |
| 381 | unknown | panel | no | yes |
| 382 | unknown | panel | no | yes |
| 383 | unknown | unknown | no | yes |
| 386 | H3A5x | unknown | no | yes |
| 391 | ? | light | no | yes |
| 392 | ? | light | no | yes |
| 393 | ? | light | no | yes |
| 396 | ? | light | no | yes |
| 398 | ? | light | no | yes |
| 399 | ? | light | no | yes |
| 401 | unknown | unknown | no | unknown |
| 402 | unknown | unknown | no | unknown |
| 403 | H1AA2 | rgbic_strip | yes | yes |
| 404 | H1AA5 | rgbic_strip | yes | yes |
| 408 | H14B1 | bulb | yes | yes |
| 411 | unknown | unknown | no | unknown |

## Out-of-scope and alias goodsTypes

164 goodsTypes are appliances, gateways, sensors, aliases of modeled SKUs, or unclassified. Listed in the raw cross-check JSON (`library-audit/` companions); omitted here for length.

