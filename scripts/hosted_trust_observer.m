/* Read-only, whitelisted observations for one disposable hosted fixture.
 * This program never asks for authorization and never writes trust or keychains.
 */
#import <Foundation/Foundation.h>
#import <Security/Security.h>
#import <Security/AuthorizationDB.h>
#include <CommonCrypto/CommonDigest.h>
#include <dlfcn.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>

static BOOL equalEnvironment(const char *name, const char *expected) {
    const char *value = getenv(name);
    return value != NULL && strcmp(value, expected) == 0;
}

static BOOL hostedContext(void) {
    return equalEnvironment("CI", "true") && equalEnvironment("GITHUB_ACTIONS", "true")
        && equalEnvironment("RUNNER_ENVIRONMENT", "github-hosted")
        && equalEnvironment("RUNNER_OS", "macOS")
        && equalEnvironment("GITHUB_EVENT_NAME", "pull_request")
        && equalEnvironment("GITHUB_JOB", "trust-lifecycle")
        && equalEnvironment("GITHUB_REPOSITORY", "victron-venus/inverter-dashboard")
        && equalEnvironment("GITHUB_WORKFLOW", "Hosted macOS trust component")
        && equalEnvironment("TRUST_COMPONENT_MODE", "admin-trust-lifecycle-v1")
        && getuid() > 0 && getuid() == geteuid();
}

static BOOL boundWorkflow(void) {
    NSDictionary *env = NSProcessInfo.processInfo.environment;
    NSString *number = env[@"TRUST_COMPONENT_PR_NUMBER"];
    NSString *source = env[@"TRUST_COMPONENT_SOURCE_SHA"];
    if (!number || !source) return NO;
    if ([number rangeOfString:@"^[1-9][0-9]{0,8}$" options:NSRegularExpressionSearch].location == NSNotFound) return NO;
    if ([source rangeOfString:@"^[a-f0-9]{40}$" options:NSRegularExpressionSearch].location == NSNotFound) return NO;
    NSString *expected = [NSString stringWithFormat:@"victron-venus/inverter-dashboard/.github/workflows/hosted-trust-component.yml@refs/pull/%@/merge", number];
    return [expected isEqualToString:env[@"GITHUB_WORKFLOW_REF"]];
}

static NSData *fixtureData(void) {
    const char *supplied = getenv("TRUST_COMPONENT_DIRECTORY");
    if (supplied == NULL) return nil;
    NSString *directory = [NSString stringWithUTF8String:supplied];
    if (directory == nil) return nil;
    NSString *pattern = @"^/Users/runner/work/_temp/native-trust-[A-Za-z0-9]{6}$";
    if ([directory rangeOfString:pattern options:NSRegularExpressionSearch].location == NSNotFound) return nil;
    char canonical[PATH_MAX];
    if (realpath(supplied, canonical) == NULL || strcmp(supplied, canonical) != 0) return nil;
    struct stat owner;
    if (lstat(supplied, &owner) != 0 || !S_ISDIR(owner.st_mode) || owner.st_uid != getuid() || (owner.st_mode & 0777) != 0700) return nil;
    NSString *filename = [directory stringByAppendingPathComponent:@"fixture.der"];
    int descriptor = open(filename.fileSystemRepresentation, O_RDONLY | O_NOFOLLOW);
    if (descriptor < 0) return nil;
    struct stat entry;
    NSData *result = nil;
    if (fstat(descriptor, &entry) == 0 && S_ISREG(entry.st_mode) && entry.st_nlink == 1
        && entry.st_uid == getuid() && entry.st_size > 0 && entry.st_size <= 65536) {
        NSMutableData *bytes = [NSMutableData dataWithLength:(NSUInteger)entry.st_size];
        ssize_t count = read(descriptor, bytes.mutableBytes, bytes.length);
        if (count == (ssize_t)bytes.length) result = bytes;
    }
    close(descriptor);
    return result;
}

static id booleanOrNull(id value) {
    if (value && CFGetTypeID((__bridge CFTypeRef)value) == CFBooleanGetTypeID()) return value;
    return [NSNull null];
}

static NSDictionary *rightSummary(const char *name) {
    CFDictionaryRef definition = NULL;
    OSStatus status = AuthorizationRightGet(name, &definition);
    NSDictionary *right = CFBridgingRelease(definition);
    NSString *kind = @"unavailable";
    id count = [NSNull null], isRoot = [NSNull null], allowRoot = [NSNull null];
    if (status == errAuthorizationSuccess && [right isKindOfClass:[NSDictionary class]]) {
        NSSet *classes = [NSSet setWithArray:@[@"allow", @"deny", @"user", @"rule"]];
        id rawClass = right[@"class"];
        kind = rawClass && [classes containsObject:rawClass] ? rawClass : @"other";
        allowRoot = booleanOrNull(right[@"allow-root"]);
        id rules = right[@"rule"];
        if ([rules isKindOfClass:[NSString class]]) {
            count = @1;
            isRoot = @([rules isEqualToString:@"is-root"]);
        } else if ([rules isKindOfClass:[NSArray class]] && [rules count] <= 1024) {
            count = @([rules count]);
            isRoot = @([rules containsObject:@"is-root"]);
        }
    }
    return @{ @"osStatus": @(status), @"class": kind, @"allowRoot": allowRoot,
              @"ruleCount": count, @"hasIsRootRule": isRoot };
}

static NSString *fingerprint(NSData *certificate) {
    unsigned char digest[CC_SHA256_DIGEST_LENGTH];
    CC_SHA256(certificate.bytes, (CC_LONG)certificate.length, digest);
    NSMutableString *result = [NSMutableString stringWithCapacity:64];
    for (size_t index = 0; index < sizeof(digest); index++) [result appendFormat:@"%02X", digest[index]];
    return result;
}

static NSDictionary *measure(NSData *data) {
    SecCertificateRef certificate = SecCertificateCreateWithData(NULL, (__bridge CFDataRef)data);
    if (!certificate) return nil;
    CFDataRef serialized = NULL;
    OSStatus adminStatus = SecTrustSettingsCreateExternalRepresentation(kSecTrustSettingsDomainAdmin, &serialized);
    id count = [NSNull null];
    // Count records, including ones whose keychain certificates are unavailable.
    // Never serialize or print the private plist, individual records or subjects.
    if (adminStatus == errSecSuccess && serialized && CFDataGetLength(serialized) <= 1048576) {
        id root = [NSPropertyListSerialization propertyListWithData:(__bridge NSData *)serialized options:NSPropertyListImmutable format:NULL error:NULL];
        if ([root isKindOfClass:[NSDictionary class]] && [root[@"trustList"] isKindOfClass:[NSDictionary class]]) count = @([root[@"trustList"] count]);
    }
    else if (adminStatus == errSecNoTrustSettings) count = @0;
    if (serialized) CFRelease(serialized);

    // Query the retained certificate directly, including after its keychain copy is deleted.
    // Enumeration alone cannot prove that the target's admin trust record disappeared.
    CFArrayRef settings = NULL;
    OSStatus targetStatus = SecTrustSettingsCopyTrustSettings(certificate, kSecTrustSettingsDomainAdmin, &settings);
    id present = [NSNull null];
    if (targetStatus == errSecSuccess) present = @YES;
    else if (targetStatus == errSecItemNotFound) present = @NO;
    if (settings) CFRelease(settings);
    CFRelease(certificate);

    // Read-only optional SPI. No CSR configuration is modified; absence stays unknown.
    typedef int (*CsrCheck)(uint32_t);
    CsrCheck check = (CsrCheck)dlsym(RTLD_DEFAULT, "csr_check");
    id sip = check ? @(check(1u << 1)) : [NSNull null]; // CSR_ALLOW_UNRESTRICTED_FS
    return @{ @"fingerprint256": fingerprint(data), @"uid": @(getuid()), @"effectiveUid": @(geteuid()),
              @"adminStatus": @(adminStatus), @"adminCount": count,
              @"targetStatus": @(targetStatus), @"targetPresent": present,
              @"sipUnrestrictedFsCheck": sip,
              @"rights": @{ @"com.apple.trust-settings.admin": rightSummary("com.apple.trust-settings.admin"),
                            @"config.modify.trust-settings": rightSummary("config.modify.trust-settings") } };
}

int main(int argc, const char *argv[]) {
    (void)argv;
    @autoreleasepool {
        if (argc != 1 || !hostedContext() || !boundWorkflow()) return 2;
        NSData *data = fixtureData();
        if (!data) return 3;
        NSDictionary *result = measure(data);
        if (!result) return 4;
        NSData *json = [NSJSONSerialization dataWithJSONObject:result options:0 error:NULL];
        if (!json || json.length > 16384) return 5;
        return fwrite(json.bytes, 1, json.length, stdout) == json.length ? 0 : 6;
    }
}
