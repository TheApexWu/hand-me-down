three.js 0.170.0 (r170), MIT licence (see LICENSE). Vendored so the screen needs no internet.
Downloaded once with curl on 22 Nov 2026:

  three.module.min.js   https://cdn.jsdelivr.net/npm/three@0.170.0/build/three.module.min.js
                        unchanged. sha256 08fd7545d13d2c7fb65ab691530a802dafefd638596501854f267d0fb13c39e7
  STLLoader.js          https://cdn.jsdelivr.net/npm/three@0.170.0/examples/jsm/loaders/STLLoader.js
                        one line changed so it loads with no import map and no build step:
                          } from 'three';   ->   } from './three.module.min.js';
                        sha256 as downloaded a0a83c88b269c94e25b690fae770d350c4728c81853195186976be7af0f8a3b3
  LICENSE               https://cdn.jsdelivr.net/npm/three@0.170.0/LICENSE

Only ../arm.js imports these, by relative path.
