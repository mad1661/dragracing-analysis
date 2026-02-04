import { config } from 'dotenv'
import { resolve } from 'path'
import { initializeApp, cert } from 'firebase-admin/app'
import { getAuth } from 'firebase-admin/auth'
import { getFirestore } from 'firebase-admin/firestore'

// Load environment variables from .env.local
config({ path: resolve(process.cwd(), '.env.local') })

// Initialize Firebase Admin
const app = initializeApp({
  credential: cert({
    projectId: process.env.FIREBASE_ADMIN_PROJECT_ID!,
    clientEmail: process.env.FIREBASE_ADMIN_CLIENT_EMAIL!,
    privateKey: process.env.FIREBASE_ADMIN_PRIVATE_KEY?.replace(/\\n/g, '\n')!,
  }),
})

const auth = getAuth(app)
const db = getFirestore(app)

async function makeUserAdmin(email: string, role: 'super_admin' | 'division_director') {
  try {
    // Get user by email
    const user = await auth.getUserByEmail(email)

    // Set custom claims for role-based access
    await auth.setCustomUserClaims(user.uid, { role })

    // Update Firestore user document
    await db.collection('users').doc(user.uid).update({
      role,
      updatedAt: new Date(),
    })

    console.log(`✅ Successfully upgraded ${email} to ${role}`)
    console.log(`User ID: ${user.uid}`)
    console.log(`\nThe user needs to sign out and sign back in for the role change to take effect.`)
  } catch (error) {
    console.error('❌ Error:', error)
  }
}

// Get email from command line argument
const email = process.argv[2]
const role = (process.argv[3] as 'super_admin' | 'division_director') || 'super_admin'

if (!email) {
  console.error('Usage: npm run make-admin <email> [super_admin|division_director]')
  process.exit(1)
}

makeUserAdmin(email, role).then(() => process.exit(0))
