/** Resolve only the pinned setup-python action's macOS CPython installation. */
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const { execFileSync } = require('node:child_process')

const HOSTED_PYTHON_ROOTS = Object.freeze({
  toolcache: '/Users/runner/hostedtoolcache/Python',
  framework: '/Library/Frameworks/Python.framework/Versions',
})
const SYSTEM_PATH = '/usr/bin:/bin:/usr/sbin:/sbin'

function noLinkedComponents(filename) {
  let current = path.parse(filename).root
  for (const part of filename.slice(current.length).split(path.sep)) {
    current = path.join(current, part)
    const entry = fs.lstatSync(current)
    assert(!entry.isSymbolicLink(), 'Canonical Python runtime must not contain symbolic links')
    assert(entry.isDirectory() || entry.isFile(), 'Python runtime path must be regular')
  }
}

function confinedTo(filename, directory) {
  const relative = path.relative(directory, filename)
  return relative && relative !== '..' && !relative.startsWith('../') && !path.isAbsolute(relative)
}

function resolveSetupPython(value, version, architecture = process.arch, roots = HOSTED_PYTHON_ROOTS) {
  assert(typeof version === 'string' && /^3\.12\.\d+$/.test(version), 'Pinned CPython 3.12 version output is required')
  assert(['arm64', 'x64'].includes(architecture), 'Unsupported native Python architecture')
  const install = path.join(roots.toolcache, version, architecture)
  const expected = path.join(install, 'bin/python')
  assert(typeof value === 'string' && path.isAbsolute(value) && value === expected, 'Interpreter must be the exact setup-python hosted toolcache output')
  // The installer deliberately links toolcache/bin to the matching macOS framework.
  // Do not follow arbitrary environment paths, symlinked toolcache ancestors or a
  // redirected framework. Resolve only this known action output, then inspect it.
  noLinkedComponents(install)
  const canonical = fs.realpathSync(value)
  const framework = path.join(roots.framework, '3.12')
  assert(confinedTo(canonical, install) || confinedTo(canonical, framework), 'Python executable escaped its approved installation')
  noLinkedComponents(canonical)
  const entry = fs.statSync(canonical)
  assert(entry.isFile() && (entry.mode & 0o111) !== 0, 'Python executable must be a regular executable file')
  return canonical
}

function validatePythonMeasurement(measured, executable, version) {
  assert(measured?.implementation === 'cpython', 'Native helper requires CPython')
  assert.deepEqual(measured.version, version.split('.').map(Number), 'Measured Python version differs from setup-python output')
  assert.equal(measured.executable, executable, 'Measured Python executable differs from the approved runtime')
}

function verifiedSetupPython(value, version) {
  const executable = resolveSetupPython(value, version)
  const measured = JSON.parse(execFileSync(executable, ['-I', '-c', 'import json, os, sys; print(json.dumps({"version": list(sys.version_info[:3]), "implementation": sys.implementation.name, "executable": os.path.realpath(sys.executable)}))'], {
    encoding: 'utf8', timeout: 10000, maxBuffer: 4096, env: { PATH: SYSTEM_PATH },
  }))
  validatePythonMeasurement(measured, executable, version)
  return executable
}

module.exports = { resolveSetupPython, validatePythonMeasurement, verifiedSetupPython }
