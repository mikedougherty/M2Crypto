"""
SSL peer certificate checking routines

Copyright (c) 2004-2007 Open Source Applications Foundation.
All rights reserved.
"""

from M2Crypto import util, EVP, m2
import re

class SSLVerificationError(Exception):
    pass

class NoCertificate(SSLVerificationError):
    pass

class WrongCertificate(SSLVerificationError):
    pass

class WrongHost(SSLVerificationError):
    def __init__(self, expectedHost, actualHost, fieldName='commonName'):
        """
        This exception will be raised if the certificate returned by the
        peer was issued for a different host than we tried to connect to.
        This could be due to a server misconfiguration or an active attack.
        
        @param expectedHost: The name of the host we expected to find in the
                             certificate.
        @param actualHost:   The name of the host we actually found in the
                             certificate.
        @param fieldName:    The field name where we noticed the error. This
                             should be either 'commonName' or 'subjectAltName'.
        """
        if fieldName not in ('commonName', 'subjectAltName'):
            raise ValueError('Unknown fieldName, should be either commonName or subjectAltName')
        
        SSLVerificationError.__init__(self)
        self.expectedHost = expectedHost
        self.actualHost = actualHost
        self.fieldName = fieldName
        
    def __str__(self):
        s = 'Peer certificate %s does not match host, expected %s, got %s' \
               % (self.fieldName, self.expectedHost, self.actualHost)
        if isinstance(s, unicode):
            s = s.encode('utf8')
        return s


class Checker:
    
    numericIpMatch = re.compile('^[0-9]+(\.[0-9]+)*$')
    
    def __init__(self, host=None, peerCertHash=None, peerCertDigest='sha1'):
        self.host = host
        self.fingerprint = peerCertHash
        self.digest = peerCertDigest

    def __call__(self, peerCert, host=None):
        if peerCert is None:
            raise NoCertificate('peer did not return certificate')

        if host is not None:
            self.host = host
        
        if self.fingerprint:
            if self.digest not in ('sha1', 'md5'):
                raise ValueError('unsupported digest "%s"' %(self.digest))

            if (self.digest == 'sha1' and len(self.fingerprint) != 40) or \
               (self.digest == 'md5' and len(self.fingerprint) != 32):
                raise WrongCertificate('peer certificate fingerprint length does not match')
            
            der = peerCert.as_der()
            md = EVP.MessageDigest(self.digest)
            md.update(der)
            digest = md.final()
            if util.octx_to_num(digest) != int(self.fingerprint, 16):
                raise WrongCertificate('peer certificate fingerprint does not match')

        if self.host:
            hostValidationPassed = False
            self.useSubjectAltNameOnly = False

            # subjectAltName=DNS:somehost[, ...]*
            try:
                subjectAltName = peerCert.get_ext('subjectAltName').get_value()
                if not self._splitSubjectAltName(self.host, subjectAltName):
                    raise WrongHost(expectedHost=self.host, 
                                    actualHost=subjectAltName,
                                    fieldName='subjectAltName')
                hostValidationPassed = True
            except LookupError:
                pass

            # commonName=somehost[, ...]*
            if not self.useSubjectAltNameOnly and not hostValidationPassed:
                hasCommonName = False
                commonNames = ''
                for entry in peerCert.get_subject().get_entries_by_nid(m2.NID_commonName):
                    hasCommonName = True
                    commonName = entry.get_data().as_text()
                    if not commonNames:
                        commonNames = commonName
                    else:
                        commonNames += ',' + commonName
                    if self._match(self.host, commonName):
                        hostValidationPassed = True
                        break

                if not hasCommonName:
                    raise WrongCertificate('no commonName in peer certificate')

                if not hostValidationPassed:
                    raise WrongHost(expectedHost=self.host,
                                    actualHost=commonNames,
                                    fieldName='commonName')

        return True

    def _splitSubjectAltName(self, host, subjectAltName):
        """
        >>> check = Checker()
        >>> check._splitSubjectAltName(host='my.example.com', subjectAltName='DNS:my.example.com')
        True
        >>> check._splitSubjectAltName(host='my.example.com', subjectAltName='DNS:*.example.com')
        True
        >>> check._splitSubjectAltName(host='my.example.com', subjectAltName='DNS:m*.example.com')
        True
        >>> check._splitSubjectAltName(host='my.example.com', subjectAltName='DNS:m*ample.com')
        False
        >>> check.useSubjectAltNameOnly
        True
        >>> check._splitSubjectAltName(host='my.example.com', subjectAltName='DNS:m*ample.com, othername:<unsupported>')
        False
        >>> check._splitSubjectAltName(host='my.example.com', subjectAltName='DNS:m*ample.com, DNS:my.example.org')
        False
        >>> check._splitSubjectAltName(host='my.example.com', subjectAltName='DNS:m*ample.com, DNS:my.example.com')
        True
        >>> check._splitSubjectAltName(host='my.example.com', subjectAltName='DNS:my.example.com, DNS:my.example.org')
        True
        >>> check.useSubjectAltNameOnly
        True
        >>> check._splitSubjectAltName(host='my.example.com', subjectAltName='')
        False
        >>> check._splitSubjectAltName(host='my.example.com', subjectAltName='othername:<unsupported>')
        False
        >>> check.useSubjectAltNameOnly
        False
        """
        self.useSubjectAltNameOnly = False
        for certHost in subjectAltName.split(','):
            certHost = certHost.lower().strip()
            if certHost[:4] == 'dns:':
                self.useSubjectAltNameOnly = True
                if self._match(host, certHost[4:]):
                    return True
        return False
        

    def _match(self, host, certHost):
        """
        >>> check = Checker()
        >>> check._match(host='my.example.com', certHost='my.example.com')
        True
        >>> check._match(host='my.example.com', certHost='*.example.com')
        True
        >>> check._match(host='my.example.com', certHost='m*.example.com')
        True
        >>> check._match(host='my.example.com', certHost='m*.EXAMPLE.com')
        True
        >>> check._match(host='my.example.com', certHost='m*ample.com')
        False
        >>> check._match(host='my.example.com', certHost='*.*.com')
        False
        >>> check._match(host='1.2.3.4', certHost='1.2.3.4')
        True
        >>> check._match(host='1.2.3.4', certHost='*.2.3.4')
        False
        >>> check._match(host='1234', certHost='1234')
        True
        """
        # XXX See RFC 2818 and 3280 for matching rules, this is may not
        # XXX yet be complete.

        host = host.lower()
        certHost = certHost.lower()

        if host == certHost:
            return True

        if certHost.count('*') > 1:
            # Not sure about this, but being conservative
            return False

        if self.numericIpMatch.match(host) or \
               self.numericIpMatch.match(certHost.replace('*', '')):
            # Not sure if * allowed in numeric IP, but think not.
            return False

        if certHost.find('\\') > -1:
            # Not sure about this, maybe some encoding might have these.
            # But being conservative for now, because regex below relies
            # on this.
            return False

        # Massage certHost so that it can be used in regex
        certHost = certHost.replace('.', '\.')
        certHost = certHost.replace('*', '[^\.]*')
        if re.compile('^%s$' %(certHost)).match(host):
            return True

        return False


if __name__ == '__main__':
    import doctest
    doctest.testmod()
